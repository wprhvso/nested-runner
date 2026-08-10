from __future__ import annotations

import datetime
import json
import logging
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nested_runner.api import ScaleSetApi
from nested_runner.budget import REST
from nested_runner.config import (
    DISPATCH_WORKERS,
    FLEET_INTERVAL,
    JOB_AVAILABLE,
    JOB_COMPLETED,
    MAX_LOOP_FAILURES,
    QUEUE_MESSAGE_TYPE,
    RATE_WAIT_CAP,
    RUN_STATUSES,
    SESSION_STATUSES,
    TOKEN_SKEW,
    max_runners,
    scale_set_name,
)
from nested_runner.crypto import encrypt
from nested_runner.errors import HttpError, NestedError, RateLimited
from nested_runner.fleet import Fleet
from nested_runner.gh import (
    cancel_run,
    current_repo,
    default_branch,
    delete_runner,
    dispatch,
    list_runners,
    list_runs,
    preflight,
)
from nested_runner.http import STOP, backoff
from nested_runner.models import Stats

if TYPE_CHECKING:
    from types import FrameType

    from nested_runner.models import Session

log = logging.getLogger("nested")

_EXIT_INTERRUPTED = 130
_UNAUTHORIZED = 401
_DAY = 86400.0


@dataclass(frozen=True)
class Ctx:
    api: ScaleSetApi
    home: str
    repo: str
    scale_set_id: int
    branch: str
    limit: int
    fleet: Fleet
    # Запуски от прошлой жизни контроллера: живые, но бесполезные.
    orphans: set[int]
    # Решать умеют два потока — главный цикл и сверщик. По одному за раз.
    scaling: threading.Lock


def install_stop_handler() -> threading.Event:
    # Тот же самый Event, на котором спит HTTP-слой: иначе сигнал приходится
    # ждать столько, сколько длится самый долгий бэкофф внутри запроса.
    stop = STOP

    def handler(signum: int, _frame: FrameType | None) -> None:
        if stop.is_set():
            log.warning("второй сигнал, выхожу немедленно")
            raise SystemExit(_EXIT_INTERRUPTED)
        log.info("сигнал %s — доработаю итерацию и уберу за собой", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handler)
    return stop


def _age(raw: object) -> str:
    if not isinstance(raw, str) or not raw:
        return ""
    text = raw.strip().rstrip("Z")
    head, dot, frac = text.partition(".")
    if dot:
        text = f"{head}.{frac[:6]}"
    try:
        stamp = datetime.datetime.fromisoformat(text)
    except ValueError:
        return ""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=datetime.UTC)
    waited = (datetime.datetime.now(datetime.UTC) - stamp).total_seconds()
    if not 0 <= waited < _DAY:
        return ""
    return f", в очереди {waited:.1f} с"


@dataclass(frozen=True)
class Batch:
    available: list[int]
    retired: int


def _read_jobs(raw_body: str) -> Batch:
    try:
        items = json.loads(raw_body or "[]")
    except json.JSONDecodeError:
        log.debug("не разобрал тело сообщения")
        return Batch([], 0)
    if not isinstance(items, list):
        return Batch([], 0)

    available: list[int] = []
    retired = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        # Сравниваем подстрокой: тип приходит и как JobAvailable, и как
        # RunnerScaleSetJobAvailable — ловим любую форму.
        kind = str(item.get("messageType") or "")
        request_id = item.get("runnerRequestId")
        log.info(
            "  %s: %s (run %s, request %s%s)",
            kind or "?",
            item.get("jobDisplayName"),
            item.get("workflowRunId"),
            request_id,
            _age(item.get("queueTime")),
        )
        if request_id and JOB_AVAILABLE in kind:
            available.append(int(request_id))
        # Отменённый job тоже приходит как JobCompleted, только раннера ему
        # никто не давал — списывать место нельзя, поедет лишний раннер.
        if JOB_COMPLETED in kind and item.get("runnerName"):
            retired += 1
    return Batch(available, retired)


def _send_runner(ctx: Ctx) -> bool:
    try:
        jit = encrypt(ctx.api.generate_jit(ctx.scale_set_id))
    except NestedError as exc:
        log.warning("не подготовил JIT: %s", exc)
        return False
    except Exception:
        log.exception("не подготовил JIT")
        return False
    if not dispatch(ctx.home, ctx.repo, jit, ctx.branch):
        return False
    ctx.fleet.born()
    return True


def _dispatch_many(ctx: Ctx, count: int) -> int:
    if count == 1:
        return int(_send_runner(ctx))
    with ThreadPoolExecutor(
        max_workers=min(count, DISPATCH_WORKERS),
        thread_name_prefix="dispatch",
    ) as pool:
        sending = [pool.submit(_send_runner, ctx) for _ in range(count)]
        return sum(int(item.result()) for item in sending)


def _scale(ctx: Ctx, stats: Stats, note: str, taken: int = 0) -> None:
    # taken — job'ы, которые мы только что забрали: статистика в сообщении их
    # ещё не видит, но раннеры им нужны уже сейчас.
    with ctx.scaling:
        want = min(ctx.limit, stats.assigned + taken)
        have = ctx.fleet.size()
        need = max(0, want - have)

        log.info("%s | %s: want=%s have=%s need=%s", stats, note, want, have, need)
        if not need:
            return

        waiting = REST.shut()
        if waiting:
            # Диспатч — это REST, и пока окно закрыто, восемь параллельных
            # попыток дадут восемь 403 и ни одного раннера. Job'ы подождут
            # в очереди: она на другом хосте и от лимита не зависит.
            log.warning(
                "нужно раннеров %s, но лимит REST закрыт ещё %.0f с (%s)",
                need,
                waiting,
                REST.state(),
            )
            return

        started = time.monotonic()
        sent = _dispatch_many(ctx, need)
        log.info(
            "раскидал раннеров %s/%s за %.1f с", sent, need, time.monotonic() - started
        )


def _acquire(ctx: Ctx, session: Session, ids: list[int]) -> int:
    if not ids:
        return 0
    try:
        taken = ctx.api.acquire_jobs(ctx.scale_set_id, session, ids)
    except HttpError as exc:
        if exc.status == _UNAUTHORIZED:
            raise
        # Job успели отменить или отдать другому — это не повод считать сессию
        # мёртвой и не повод ломать цикл: остаток подберёт страховка на тишине.
        log.warning("не забрал job'ы %s: %s", ids, exc)
        return 0
    log.info("забрал job'ов: %s из %s", taken, len(ids))
    return taken


def _handle(ctx: Ctx, session: Session, message: dict[str, Any]) -> None:
    kind = message.get("messageType")
    if kind != QUEUE_MESSAGE_TYPE:
        log.debug("пропускаю сообщение типа %r", kind)
        return
    # Статистика приезжает внутри сообщения — она свежая по определению,
    # отдельный GET на scale set врёт и стоит целого цикла long poll.
    raw = message.get("statistics")
    if raw is None:
        # Такого быть не должно, но принять нули значит молча съесть job.
        log.warning("в сообщении нет статистики, спрашиваю scale set")
        stats = ctx.api.statistics(ctx.scale_set_id)
    else:
        stats = Stats.parse(raw)
    batch = _read_jobs(str(message.get("body") or ""))
    # Сначала забрать: если сорвётся, сообщение придёт снова, и списывать
    # отработавших второй раз не придётся.
    taken = _acquire(ctx, session, batch.available)
    ctx.fleet.retired(batch.retired)
    _scale(ctx, stats, "сообщение", taken)


def _pick_up(ctx: Ctx, session: Session, stats: Stats, note: str) -> None:
    taken = 0
    if stats.available or stats.assigned:
        ids = [
            int(job["runnerRequestId"])
            for job in ctx.api.acquirable_jobs(ctx.scale_set_id)
            if job.get("runnerRequestId")
        ]
        taken = _acquire(ctx, session, ids)
    _scale(ctx, stats, note, taken)


def _idle(ctx: Ctx, session: Session) -> None:
    # Очередь молчит: подстраховываемся от потерянного сообщения. Это уже не
    # горячий путь, тут лишние запросы не страшны.
    _pick_up(ctx, session, ctx.api.statistics(ctx.scale_set_id), "тишина")


def _ack(ctx: Ctx, session: Session, message: dict[str, Any]) -> None:
    message_id = message.get("messageId")
    if message_id is None:
        return
    try:
        ctx.api.delete_message(session, int(message_id))
    except NestedError as exc:
        # Подтверждаем уже после решения: round trip не должен стоять
        # между сообщением и раскидыванием раннеров.
        log.warning("не подтвердил сообщение %s: %s", message_id, exc)


def _alive_runs(ctx: Ctx) -> set[int]:
    found = {
        run_id
        for state in RUN_STATUSES
        for run_id in list_runs(ctx.home, ctx.repo, state)
    }
    return found - ctx.orphans


def _reconcile(ctx: Ctx) -> None:
    ctx.fleet.observe(_alive_runs(ctx))


def _worth_it(ctx: Ctx) -> bool:
    """Стоит ли платить за список запусков прямо сейчас."""
    if not ctx.fleet.tracking():
        return False
    if REST.spend(f"сверка {ctx.repo}", len(RUN_STATUSES)):
        return True
    log.debug("сверку придержал бюджет REST: %s", REST.state())
    return False


def _evict(ctx: Ctx) -> None:
    # Scale set создан заново — раннеры прошлой жизни зарегистрированы
    # в удалённом и job не возьмут. Считать их ёмкостью значит заставить
    # первые job'ы ждать, пока догорят чужие запуски.
    stale = _alive_runs(ctx)
    if not stale:
        return
    ctx.orphans.update(stale)
    cancelled = sum(cancel_run(ctx.home, run_id) for run_id in stale)
    log.info("осиротевших запусков: %s, отменил %s", len(stale), cancelled)


def _reconcile_loop(ctx: Ctx, stop: threading.Event) -> None:
    while not stop.wait(FLEET_INTERVAL):
        try:
            if _worth_it(ctx):
                _reconcile(ctx)
            # Решение принимаем каждый круг, даже когда список запусков решили
            # не спрашивать: статистика приходит из pipeline API, у которого
            # своего лимита нет. Ёмкость меняется и без нас — догорел запуск,
            # умер диспатч, — а главный цикл в это время спит в long poll, и
            # без решения тут job простоял бы до конца окна опроса.
            _scale(ctx, ctx.api.statistics(ctx.scale_set_id), "сверка")
        except RateLimited as exc:
            log.debug("сверка ждёт сброса лимита: %s", exc)
        except NestedError as exc:
            log.debug("не сверил флот: %s", exc)
        except Exception:
            log.exception("сверка флота сорвалась")


def _fresh(ctx: Ctx, session: Session) -> Session:
    if time.time() < session.queue_token_exp - TOKEN_SKEW:
        return session
    log.debug("токен очереди на исходе, обновляю заранее")
    return ctx.api.refresh_session(ctx.scale_set_id, session)


def _recover(
    ctx: Ctx, session: Session, owner: str, status: int
) -> tuple[Session, int]:
    # Ничего не выбрасываем: исключение из except-ветки ушло бы мимо счётчика
    # попыток, вынесло бы цикл целиком и утащило за собой всех живых раннеров.
    if status == _UNAUTHORIZED:
        try:
            return ctx.api.refresh_session(ctx.scale_set_id, session), -1
        except NestedError as exc:
            log.info("токен очереди не обновился: %s", exc)
    log.info("сессия недействительна (%s), пересоздаю", status)
    try:
        return ctx.api.reopen_session(ctx.scale_set_id, session, owner), 0
    except NestedError as exc:
        log.warning("сессия не пересоздалась: %s", exc)
        return session, -1


def _hold(exc: RateLimited, stop: threading.Event) -> None:
    # Окно ждём целиком, но кусками: проспать час одним махом значит не заметить
    # ни остановки, ни того, что лимит открылся раньше обещанного.
    waiting = min(max(exc.retry_in, 1.0), RATE_WAIT_CAP)
    log.warning("лимит REST выбран, жду %.0f с — %s", waiting, REST.state())
    stop.wait(waiting)


def _tick(ctx: Ctx, session: Session, last_id: int, stop: threading.Event) -> int:
    try:
        message = ctx.api.poll(session, last_id, ctx.limit)
    except HttpError:
        raise
    except NestedError as exc:
        # Опрос сорвался — решение всё равно принимаем, иначе один сорванный
        # long poll стоит целого окна тишины. Пауза тут затем, чтобы мгновенно
        # падающий опрос не раскрутил цикл в молотилку по API.
        log.warning("опрос очереди не удался: %s", exc)
        stop.wait(backoff(1))
        message = None

    if stop.is_set():
        return last_id
    if message is None:
        _idle(ctx, session)
        return last_id

    fresh_id = int(message.get("messageId") or 0)
    if fresh_id and fresh_id <= last_id:
        # Уже обработали в прошлый раз, просто не подтвердили.
        log.debug("сообщение %s уже обработано, подтверждаю снова", fresh_id)
        _ack(ctx, session, message)
        return last_id

    _handle(ctx, session, message)
    _ack(ctx, session, message)
    return fresh_id or last_id


def _cleanup(ctx: Ctx, session: Session | None) -> None:
    # Сначала бесплатное и главное. Сессия и scale set живут в pipeline API,
    # REST-лимит их не трогает, а без scale set живые раннеры всё равно ничего
    # не возьмут — это и есть основная часть уборки.
    if session is not None:
        try:
            ctx.api.close_session(ctx.scale_set_id, session)
        except NestedError as exc:
            log.warning("сессия не закрылась: %s", exc)

    removed_set = False
    try:
        ctx.api.delete_scale_set(ctx.scale_set_id)
        removed_set = True
    except NestedError as exc:
        log.warning("scale set не удалён: %s", exc)

    waiting = REST.shut()
    if waiting:
        # Гасить запуски нечем: лимит выбран, и полсотни заведомо отказанных
        # запросов ничего не изменят. Раннеры эфемерные и упрутся в свой
        # timeout-minutes сами.
        log.warning(
            "лимит REST закрыт ещё %.0f с — запуски и раннеров не подчищаю "
            "(scale-set-removed=%s), они догорят сами",
            waiting,
            removed_set,
        )
        return

    cancelled = 0
    try:
        for state in RUN_STATUSES:
            for run_id in list_runs(ctx.home, ctx.repo, state):
                if cancel_run(ctx.home, run_id):
                    cancelled += 1
    except NestedError as exc:
        log.warning("не перечислил запуски раннеров: %s", exc)

    removed = 0
    try:
        removed = sum(
            delete_runner(ctx.repo, int(item["id"])) for item in list_runners(ctx.repo)
        )
    except NestedError as exc:
        log.warning("не перечислил раннеров: %s", exc)

    log.info(
        "убрал за собой: cancelled=%s scale-set-removed=%s runners-removed=%s",
        cancelled,
        removed_set,
        removed,
    )


def _start(repo: str) -> tuple[Ctx, bool]:
    home = current_repo()
    preflight(repo, home)

    api = ScaleSetApi(repo)
    name = scale_set_name()
    scale_set, created = api.ensure_scale_set(name)
    ctx = Ctx(
        api=api,
        home=home,
        repo=repo,
        scale_set_id=int(scale_set["id"]),
        branch=default_branch(home),
        limit=max_runners(),
        fleet=Fleet(),
        orphans=set(),
        scaling=threading.Lock(),
    )

    log.info(
        "поехали: цель=%s раннеры=%s scale-set=%r id=%s max=%s ветка=%s",
        repo,
        home,
        name,
        ctx.scale_set_id,
        ctx.limit,
        ctx.branch,
    )
    log.info("остановка: Ctrl+C — раннеры и scale set будут снесены")
    return ctx, created


def run(repo: str, stop: threading.Event) -> int:
    ctx, created = _start(repo)
    owner = f"nested-{threading.get_ident()}"

    session: Session | None = None
    last_id = 0
    failures = 0
    # Свой стоп-сигнал: соседние цели живут своей жизнью, и сверщик этой
    # не должен тикать по API после того, как её цикл закончился.
    done = threading.Event()
    watcher = threading.Thread(
        target=_reconcile_loop,
        args=(ctx, done),
        name=f"{repo}~флот",
        daemon=True,
    )

    try:
        try:
            _evict(ctx) if created else _reconcile(ctx)
        except NestedError as exc:
            log.warning("не разобрался с запусками на старте: %s", exc)

        session = ctx.api.reopen_session(ctx.scale_set_id, None, owner)
        watcher.start()
        # Сессия отдаёт статистику сразу — не ждём первого сообщения.
        _pick_up(ctx, session, session.stats, "старт")

        while not stop.is_set():
            try:
                session = _fresh(ctx, session)
                last_id = _tick(ctx, session, last_id, stop)
                failures = 0

            except RateLimited as exc:
                # Сбоем это не считаем и сессию не трогаем: очередь job'ов
                # живёт на другом хосте и про REST-лимит ничего не знает.
                # Рвать её тут значит платить за пересоздание теми запросами,
                # которых как раз и не осталось.
                failures = 0
                _hold(exc, stop)

            except HttpError as exc:
                if exc.status not in SESSION_STATUSES:
                    raise
                failures += 1
                if failures >= MAX_LOOP_FAILURES:
                    raise
                session, reset = _recover(ctx, session, owner, exc.status)
                if reset >= 0:
                    last_id = reset
                if failures > 1:
                    # Штатное протухание токена лечим сразу, но заклинившую
                    # сессию перестаём дёргать без передышки.
                    stop.wait(backoff(failures))

            except NestedError as exc:
                failures += 1
                if failures >= MAX_LOOP_FAILURES:
                    raise
                delay = backoff(failures)
                log.warning(
                    "итерация не удалась (%s/%s): %s",
                    failures,
                    MAX_LOOP_FAILURES,
                    exc,
                )
                stop.wait(delay)
    finally:
        done.set()
        _cleanup(ctx, session)

    return 0
