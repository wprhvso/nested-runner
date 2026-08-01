"""Клиент недокументированного GitHub Actions runner scale set API.

Публичный REST (registration token, dispatch, list runs) идёт через `gh`,
чтобы не тащить свою аутентификацию. Всё, что живёт на pipelines*.actions.
githubusercontent.com, — через urllib, потому что там своя схема авторизации
(RemoteAuth / Bearer с отдельным JWT), которую `gh` подменить не даст.

API помечен как 6.0-preview и не имеет гарантий совместимости.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import random
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable

API_VERSION = "6.0-preview"
USER_AGENT = "nested-runner/2"

SCALE_SET_NAME = os.environ.get("NESTED_SCALE_SET", "nested")
MAX_RUNNERS = int(os.environ.get("NESTED_MAX", "10"))
RUNNER_WORKFLOW = os.environ.get("NESTED_WORKFLOW", "runner.yml")

# Долгий poll на стороне сервиса ~50 с; таймаут клиента должен быть заметно больше.
POLL_TIMEOUT = 90
REQUEST_TIMEOUT = 30
GH_TIMEOUT = 60

# Насколько раньше истечения обновлять токены.
TOKEN_SKEW = 300

# Сколько секунд считать только что отправленный dispatch «ещё не видимым» в API.
DISPATCH_GRACE = 90

MAX_ATTEMPTS = 5
BACKOFF_BASE = 1.5
BACKOFF_CAP = 30

RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

log = logging.getLogger("nested")


class NestedError(Exception):
    """Ошибка, которую имеет смысл показать пользователю без трейсбека."""


class HttpError(NestedError):
    def __init__(self, status: int, url: str, body: str) -> None:
        self.status = status
        self.url = url
        self.body = body[:500]
        super().__init__(f"HTTP {status} на {_redact(url)}: {self.body}")


def _redact(text: str) -> str:
    """Убирает из строки opaque-сегмент pipeline URL и токены."""
    text = re.sub(r"(sessionId=)[0-9a-f-]+", r"\1<session>", text)
    return re.sub(r"(githubusercontent\.com/)[^/]+", r"\1<opaque>", text)


# --------------------------------------------------------------------------- gh


def gh(*args: str, check: bool = True, timeout: int = GH_TIMEOUT) -> str:
    """Вызывает gh без shell. Возвращает stdout."""
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise NestedError("gh не найден — ставь: https://cli.github.com") from None
    except subprocess.TimeoutExpired:
        raise NestedError(f"gh {' '.join(args[:2])} не ответил за {timeout} с") from None

    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise NestedError(f"gh {' '.join(args[:2])} упал: {detail}")
    return proc.stdout


def gh_json(*args: str, default: Any = None) -> Any:
    out = gh(*args, check=default is None)
    if not out.strip():
        return default
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        if default is not None:
            return default
        raise NestedError(f"gh {' '.join(args[:2])} вернул не JSON") from None


# ------------------------------------------------------------------------- http


def _sleep_for(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(float(retry_after), BACKOFF_CAP)
        except ValueError:
            pass
    return min(BACKOFF_BASE**attempt, BACKOFF_CAP) * (0.5 + random.random())


def request(
    method: str,
    url: str,
    *,
    auth: str | None = None,
    body: Any = None,
    timeout: int = REQUEST_TIMEOUT,
    attempts: int = MAX_ATTEMPTS,
) -> Any:
    """HTTP с ретраями на транзиентных ошибках. Возвращает разобранный JSON или None.

    401 и 404 не ретраятся: их разбирает вызывающий код (рефреш токена,
    отсутствие scale set). Пустое тело (в том числе 202 от long poll) -> None.
    """
    payload = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if auth:
        headers["Authorization"] = auth

    last: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            if exc.code not in RETRY_STATUSES:
                raise HttpError(exc.code, url, detail) from None
            last = HttpError(exc.code, url, detail)
            delay = _sleep_for(attempt, exc.headers.get("Retry-After"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            delay = _sleep_for(attempt, None)

        if attempt + 1 < attempts:
            log.debug("повтор %s %s через %.1f с (%s)", method, _redact(url), delay, last)
            time.sleep(delay)

    raise NestedError(f"{method} {_redact(url)} не удался за {attempts} попыток: {last}")


def jwt_expiry(token: str) -> float:
    """Достаёт exp из JWT без проверки подписи. При неудаче — консервативные 30 минут."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, ValueError, binascii.Error):
        log.debug("не разобрал exp из токена, считаю его коротким")
        return time.time() + 1800


# -------------------------------------------------------------------------- api


@dataclass(frozen=True)
class Stats:
    available: int = 0
    acquired: int = 0
    assigned: int = 0
    running: int = 0
    registered: int = 0
    busy: int = 0
    idle: int = 0

    @classmethod
    def parse(cls, raw: dict[str, Any] | None) -> "Stats":
        raw = raw or {}
        return cls(
            available=raw.get("totalAvailableJobs", 0),
            acquired=raw.get("totalAcquiredJobs", 0),
            assigned=raw.get("totalAssignedJobs", 0),
            running=raw.get("totalRunningJobs", 0),
            registered=raw.get("totalRegisteredRunners", 0),
            busy=raw.get("totalBusyRunners", 0),
            idle=raw.get("totalIdleRunners", 0),
        )

    def __str__(self) -> str:
        return (
            f"available={self.available} acquired={self.acquired} "
            f"assigned={self.assigned} running={self.running} "
            f"runners={self.registered} busy={self.busy} idle={self.idle}"
        )


@dataclass
class Session:
    session_id: str
    queue_url: str
    queue_token: str
    queue_token_exp: float


class ScaleSetApi:
    """Тонкая обёртка над _apis/runtime/runnerscalesets для одного репозитория."""

    def __init__(self, repo: str) -> None:
        self.repo = repo
        self._api_base = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
        self._server = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        self._pipeline_url: str | None = None
        self._token: str = ""
        self._token_exp: float = 0.0

    # -- аутентификация ----------------------------------------------------

    def _registration_token(self) -> str:
        return gh(
            "api",
            "-X",
            "POST",
            f"repos/{self.repo}/actions/runners/registration-token",
            "--jq",
            ".token",
        ).strip()

    def _authenticate(self) -> None:
        """Меняет registration token на JWT Actions-сервиса и pipeline URL."""
        info = request(
            "POST",
            f"{self._api_base}/actions/runner-registration",
            auth=f"RemoteAuth {self._registration_token()}",
            body={"url": f"{self._server}/{self.repo}", "runnerEvent": "register"},
        )
        if not info or "token" not in info or "url" not in info:
            raise NestedError("runner-registration не вернул token/url")

        self._token = info["token"]
        self._token_exp = jwt_expiry(self._token)
        self._pipeline_url = info["url"].rstrip("/")
        log.debug("получен pipeline JWT, годен ещё %.0f мин", (self._token_exp - time.time()) / 60)

    @property
    def token(self) -> str:
        if not self._token or time.time() > self._token_exp - TOKEN_SKEW:
            self._authenticate()
        return self._token

    @property
    def pipeline_url(self) -> str:
        if self._pipeline_url is None:
            self._authenticate()
        assert self._pipeline_url is not None
        return self._pipeline_url

    def call(self, method: str, path: str, *, body: Any = None, **kw: Any) -> Any:
        """Запрос к pipeline API с автоматическим рефрешем JWT на 401."""
        url = f"{self.pipeline_url}/_apis/runtime/{path}"
        url += ("&" if "?" in url else "?") + f"api-version={API_VERSION}"
        try:
            return request(method, url, auth=f"Bearer {self.token}", body=body, **kw)
        except HttpError as exc:
            if exc.status != 401:
                raise
            log.info("pipeline JWT отвергнут, переполучаю")
            self._authenticate()
            return request(method, url, auth=f"Bearer {self.token}", body=body, **kw)

    # -- scale set ---------------------------------------------------------

    def find_scale_set(self, name: str) -> dict[str, Any] | None:
        query = urllib.parse.quote(name, safe="")
        try:
            found = self.call("GET", f"runnerscalesets?name={query}")
        except HttpError as exc:
            if exc.status == 404:
                return None
            raise
        values = found.get("value", []) if isinstance(found, dict) else (found or [])
        for item in values:
            if item.get("name") == name:
                return item
        return None

    def ensure_scale_set(self, name: str) -> dict[str, Any]:
        existing = self.find_scale_set(name)
        if existing:
            log.info("scale set %r уже есть, id=%s", name, existing["id"])
            return existing

        created = self.call(
            "POST",
            "runnerscalesets",
            body={
                "name": name,
                # На уровне репозитория есть только группа Default.
                "runnerGroupId": 1,
                "runnerSetting": {"ephemeral": True, "disableUpdate": True},
            },
        )
        log.info("создал scale set %r, id=%s", name, created["id"])
        return created

    def delete_scale_set(self, scale_set_id: int) -> None:
        self.call("DELETE", f"runnerscalesets/{scale_set_id}")

    def statistics(self, scale_set_id: int) -> Stats:
        raw = self.call("GET", f"runnerscalesets/{scale_set_id}")
        return Stats.parse(raw.get("statistics"))

    # -- сессия и очередь --------------------------------------------------

    def open_session(self, scale_set_id: int, owner: str) -> Session:
        raw = self.call(
            "POST",
            f"runnerscalesets/{scale_set_id}/sessions",
            body={"ownerName": owner},
        )
        token = raw["messageQueueAccessToken"]
        session = Session(
            session_id=raw["sessionId"],
            queue_url=raw["messageQueueUrl"],
            queue_token=token,
            queue_token_exp=jwt_expiry(token),
        )
        log.info("открыл message session %s", session.session_id)
        return session

    def close_session(self, scale_set_id: int, session: Session) -> None:
        self.call(
            "DELETE",
            f"runnerscalesets/{scale_set_id}/sessions/{session.session_id}",
        )
        log.info("закрыл message session %s", session.session_id)

    def poll(self, session: Session, last_message_id: int | None) -> dict[str, Any] | None:
        """Долгий poll очереди. None — сообщений нет (202) или таймаут сервиса."""
        url = session.queue_url
        if last_message_id is not None:
            url += f"&lastMessageId={last_message_id}"
        # Ретраи здесь короче: цикл всё равно вернётся сюда через мгновение.
        return request(
            "GET",
            url,
            auth=f"Bearer {session.queue_token}",
            timeout=POLL_TIMEOUT,
            attempts=2,
        )

    def delete_message(self, session: Session, message_id: int) -> None:
        base, _, query = session.queue_url.partition("?")
        request(
            "DELETE",
            f"{base}/{message_id}?{query}",
            auth=f"Bearer {session.queue_token}",
            attempts=3,
        )

    # -- jobs --------------------------------------------------------------

    def acquirable_jobs(self, scale_set_id: int) -> list[dict[str, Any]]:
        raw = self.call("GET", f"runnerscalesets/{scale_set_id}/acquirablejobs")
        if not raw:
            return []
        return raw.get("value", []) if isinstance(raw, dict) else raw

    def acquire(self, scale_set_id: int, request_id: int) -> None:
        self.call(
            "POST",
            f"runnerscalesets/{scale_set_id}/jobs/{request_id}/acquire",
            body={},
        )


# -------------------------------------------------------------------- ledger


@dataclass
class Ledger:
    """Учёт «раннеров в пути»: dispatch отправлен, но run ещё не виден в API."""

    repo: str
    recent: list[float] = field(default_factory=list)

    def record(self) -> None:
        self.recent.append(time.monotonic())

    def pending(self) -> int:
        cutoff = time.monotonic() - DISPATCH_GRACE
        self.recent = [t for t in self.recent if t > cutoff]

        counted = 0
        for state in ("queued", "in_progress"):
            runs = gh_json(
                "run", "list",
                "--repo", self.repo,
                "--workflow", RUNNER_WORKFLOW,
                "--status", state,
                "--limit", "100",
                "--json", "databaseId",
                default=[],
            )
            counted += len(runs)

        # API может ещё не показывать самые свежие dispatch — берём максимум,
        # а не сумму, чтобы не посчитать один и тот же run дважды.
        return max(counted, len(self.recent))


def dispatch(repo: str, scale_set_id: int, branch: str, ledger: Ledger) -> bool:
    try:
        gh(
            "workflow", "run", RUNNER_WORKFLOW,
            "--repo", repo,
            "--ref", branch,
            "-f", f"scale_set_id={scale_set_id}",
        )
    except NestedError as exc:
        log.error("не удалось запустить %s: %s", RUNNER_WORKFLOW, exc)
        return False
    ledger.record()
    return True


# ---------------------------------------------------------------- preflight


def preflight(repo: str) -> None:
    if not re.fullmatch(r"[^/\s]+/[^/\s]+", repo):
        raise NestedError(f"ожидается owner/name, получено: {repo}")
    if shutil.which("gh") is None:
        raise NestedError("gh не найден — ставь: https://cli.github.com")

    gh("auth", "status")
    gh("api", f"repos/{repo}")
    gh("api", f"repos/{repo}/actions/workflows/{RUNNER_WORKFLOW}")
    gh("api", f"repos/{repo}/actions/runners?per_page=1")


def ensure_secret(repo: str) -> None:
    names = gh_json("secret", "list", "--repo", repo, "--json", "name", default=[])
    if any(item.get("name") == "RUNNER_PAT" for item in names):
        return

    token = gh("auth", "token").strip()
    try:
        subprocess.run(
            ["gh", "secret", "set", "RUNNER_PAT", "--repo", repo],
            input=token,
            text=True,
            capture_output=True,
            timeout=GH_TIMEOUT,
            check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise NestedError(
            "нет секрета RUNNER_PAT и не смог его поставить\n"
            "сделай fine-grained PAT (Actions rw, Administration rw, Contents r) и:\n"
            f"  gh secret set RUNNER_PAT --repo {repo}"
        ) from None
    log.info("положил RUNNER_PAT из gh auth token")


def default_branch(repo: str) -> str:
    return gh("api", f"repos/{repo}", "--jq", ".default_branch").strip()


# --------------------------------------------------------------------- loop


def _install_stop_handler() -> threading.Event:
    stop = threading.Event()

    def handler(signum: int, _frame: Any) -> None:
        if stop.is_set():
            log.warning("второй сигнал, выхожу немедленно")
            raise SystemExit(130)
        log.info("сигнал %s — доработаю итерацию и закрою сессию", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handler)
    return stop


def _log_job_messages(raw_body: str) -> None:
    """Тело сообщения — строка с JSON-массивом внутри, отсюда двойной разбор."""
    try:
        for item in json.loads(raw_body or "[]"):
            log.info(
                "  %s: %s (run %s, request %s)",
                item.get("messageType"),
                item.get("jobDisplayName"),
                item.get("workflowRunId"),
                item.get("runnerRequestId"),
            )
    except json.JSONDecodeError:
        log.debug("не разобрал тело сообщения")


def run(repo: str) -> int:
    preflight(repo)
    ensure_secret(repo)

    api = ScaleSetApi(repo)
    scale_set = api.ensure_scale_set(SCALE_SET_NAME)
    scale_set_id = int(scale_set["id"])
    branch = default_branch(repo)
    ledger = Ledger(repo)
    stop = _install_stop_handler()

    log.info(
        "поехали: %s scale-set=%r id=%s max=%s ветка=%s",
        repo, SCALE_SET_NAME, scale_set_id, MAX_RUNNERS, branch,
    )
    log.info("остановка: Ctrl+C закроет сессию, но раннеров не тронет — нужен `just stop %s`", repo)

    session = api.open_session(scale_set_id, owner=f"nested-{os.getpid()}")
    last_message_id: int | None = None

    try:
        while not stop.is_set():
            # 1. Долгий poll. Он же переводит acquired -> assigned.
            try:
                message = api.poll(session, last_message_id)
            except HttpError as exc:
                if exc.status not in (401, 404):
                    raise
                log.info("сессия недействительна (%s), пересоздаю", exc.status)
                session = api.open_session(scale_set_id, owner=f"nested-{os.getpid()}")
                last_message_id = None
                continue
            except NestedError as exc:
                log.warning("poll не удался: %s", exc)
                time.sleep(5)
                continue

            if message:
                last_message_id = message.get("messageId")
                _log_job_messages(message.get("body", ""))
                if last_message_id is not None:
                    try:
                        api.delete_message(session, last_message_id)
                    except NestedError as exc:
                        log.warning("не удалось подтвердить сообщение: %s", exc)

            if stop.is_set():
                break

            # 2. Забираем доступные jobs — до потолка, чтобы не хоардить чужое.
            stats = api.statistics(scale_set_id)
            for job in api.acquirable_jobs(scale_set_id)[: max(0, MAX_RUNNERS - stats.acquired)]:
                request_id = job.get("runnerRequestId")
                if request_id is None:
                    continue
                try:
                    api.acquire(scale_set_id, int(request_id))
                    log.info("забрал job %s (%s)", request_id, job.get("jobDisplayName"))
                except NestedError as exc:
                    log.warning("не забрал job %s: %s", request_id, exc)

            # 3. Считаем, сколько матрёшек не хватает.
            stats = api.statistics(scale_set_id)
            pending = ledger.pending()
            waiting = max(0, stats.acquired - stats.running)
            need = max(0, waiting - stats.idle - pending)
            room = max(0, MAX_RUNNERS - stats.registered - pending)
            launch = min(need, room)

            sent = sum(dispatch(repo, scale_set_id, branch, ledger) for _ in range(launch))
            log.info("%s | pending=%s need=%s dispatched=%s", stats, pending, need, sent)
    finally:
        try:
            api.close_session(scale_set_id, session)
        except NestedError as exc:
            log.warning("сессия не закрылась: %s", exc)

    log.info("вышел; раннеры и scale set на месте — `just stop %s`, если они не нужны", repo)
    return 0


def stop_all(repo: str) -> int:
    preflight(repo)

    cancelled = 0
    for state in ("in_progress", "queued"):
        runs = gh_json(
            "run", "list",
            "--repo", repo,
            "--workflow", RUNNER_WORKFLOW,
            "--status", state,
            "--limit", "100",
            "--json", "databaseId",
            default=[],
        )
        for item in runs:
            try:
                gh("run", "cancel", str(item["databaseId"]), "--repo", repo)
                cancelled += 1
            except NestedError as exc:
                log.warning("не отменил run %s: %s", item["databaseId"], exc)

    api = ScaleSetApi(repo)
    removed_set = False
    try:
        scale_set = api.find_scale_set(SCALE_SET_NAME)
        if scale_set:
            api.delete_scale_set(int(scale_set["id"]))
            removed_set = True
    except NestedError as exc:
        log.warning("scale set не удалён: %s", exc)

    removed = 0
    runners = gh_json("api", f"repos/{repo}/actions/runners", default={}) or {}
    for runner in runners.get("runners", []):
        try:
            gh("api", "-X", "DELETE", f"repos/{repo}/actions/runners/{runner['id']}")
            removed += 1
        except NestedError:
            pass  # эфемерные раннеры часто отцепляются сами

    log.info("cancelled=%s scale-set-removed=%s runners-removed=%s", cancelled, removed_set, removed)
    return 0


def status(repo: str) -> int:
    preflight(repo)
    api = ScaleSetApi(repo)
    scale_set = api.find_scale_set(SCALE_SET_NAME)
    if not scale_set:
        log.info("scale set %r не найден", SCALE_SET_NAME)
        return 0
    log.info("scale set %r id=%s", SCALE_SET_NAME, scale_set["id"])
    log.info("%s", api.statistics(int(scale_set["id"])))
    return 0


def main(argv: Iterable[str]) -> int:
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("NESTED_DEBUG") else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    args = list(argv)
    if len(args) != 2:
        print("usage: nested.py {run|stop|status} owner/name", file=sys.stderr)
        return 2

    command, repo = args
    handlers = {"run": run, "stop": stop_all, "status": status}
    if command not in handlers:
        print(f"неизвестная команда: {command}", file=sys.stderr)
        return 2

    try:
        return handlers[command](repo)
    except NestedError as exc:
        log.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
