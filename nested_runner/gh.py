from __future__ import annotations

import functools
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nested_runner.budget import REST
from nested_runner.config import (
    DISPATCH_ATTEMPTS,
    GH_TIMEOUT,
    NOT_MODIFIED,
    PREFLIGHT_TTL,
    REPO_PATTERN,
    REST_VERSION,
    RUNNER_NAME_PREFIX,
    RUNS_PAGES,
    RUNS_PER_PAGE,
    api_base,
    home_repo,
    home_repo_configured,
    runner_workflow,
)
from nested_runner.crypto import check_age, recipient
from nested_runner.errors import HttpError, NestedError, RateLimited
from nested_runner.http import fetch, guard, request

if TYPE_CHECKING:
    from collections.abc import Callable

log = logging.getLogger("nested")

_INSTALL_HINT = "gh не найден — ставь: https://cli.github.com"
_REST_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": REST_VERSION,
}
_GONE = 409

# ETag на каждый опрашиваемый URL плюс уже отжатый из него результат.
_STORE: dict[str, tuple[str, Any]] = {}
_STORE_LOCK = threading.Lock()

# Когда в последний раз проверяли пару (цель, home).
_CHECKED: dict[tuple[str, str], float] = {}


def gh(*args: str, check: bool = True, timeout: int = GH_TIMEOUT) -> str:
    try:
        proc = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        raise NestedError(_INSTALL_HINT) from None
    except subprocess.TimeoutExpired:
        raise NestedError(
            f"gh {' '.join(args[:2])} не ответил за {timeout} с"
        ) from None

    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise NestedError(f"gh {' '.join(args[:2])} упал: {detail}")
    return proc.stdout


@functools.cache
def token() -> str:
    value = os.environ.get("GH_TOKEN", "").strip() or gh("auth", "token").strip()
    if not value:
        raise NestedError("не нашёл токен: ни GH_TOKEN, ни gh auth token")
    return value


def _url(path: str) -> str:
    return f"{api_base()}/{path.lstrip('/')}"


def rest(method: str, path: str, *, body: object = None, attempts: int = 3) -> Any:
    return request(
        method,
        _url(path),
        auth=f"Bearer {token()}",
        body=body,
        attempts=attempts,
        extra=_REST_HEADERS,
        budget=REST,
    )


def polled[T](path: str, tag: str, pick: Callable[[Any], T]) -> T:
    """GET, который платит за изменения, а не за опрос.

    Условный запрос GitHub в лимит не засчитывает, если ответил 304, — значит
    один и тот же список можно крутить сколько угодно, пока он не поменялся.
    В памяти держим уже отжатое значение, а не сырой ответ: страница запусков
    весит мегабайты, а нужны из неё идентификаторы.

    tag разделяет потребителей одного URL: страницы запусков у всех целей
    общие, а отжатое из них — у каждой своё.
    """
    url = _url(path)
    key = f"{tag} {url}"
    with _STORE_LOCK:
        known = _STORE.get(key)

    reply = fetch(
        "GET",
        url,
        auth=f"Bearer {token()}",
        attempts=3,
        extra=_REST_HEADERS,
        etag=known[0] if known else None,
        budget=REST,
    )
    if reply.status == NOT_MODIFIED and known is not None:
        return known[1]

    value = pick(reply.payload)
    if reply.etag:
        with _STORE_LOCK:
            _STORE[key] = (reply.etag, value)
    return value


def _reachable(_: Any) -> None:
    """Проба: важно, что запрос не упал, а не что он вернул."""
    return


@functools.cache
def current_repo() -> str:
    # За жизнь процесса home-репо не меняется, а gh repo view — это и лишний
    # процесс, и лишний запрос из того же лимита.
    fallback = home_repo()
    if home_repo_configured():
        return fallback
    try:
        out = gh(
            "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"
        ).strip()
    except NestedError:
        out = ""
    return out or fallback


def _fresh_enough(repo: str, home: str) -> bool:
    at = _CHECKED.get((repo, home))
    return at is not None and time.monotonic() - at < PREFLIGHT_TTL


def preflight(repo: str, home: str) -> None:
    if not REPO_PATTERN.fullmatch(repo):
        raise NestedError(f"ожидается owner/name, получено: {repo}")
    if not REPO_PATTERN.fullmatch(home):
        raise NestedError(f"GH_REPO должен быть owner/name, получено: {home}")
    if shutil.which("gh") is None:
        raise NestedError(_INSTALL_HINT)

    check_age()
    log.debug("публичный ключ: %s", recipient())

    if _fresh_enough(repo, home):
        # Контроллер перезапускается на каждом сбое, а права и workflow за это
        # время не меняются. Гонять пробы заново на каждом круге — ровно тот
        # расход, из-за которого лимит и кончается.
        log.debug("проверки на старте ещё свежие, не повторяю")
        return

    # Проверяем до gh auth status: он тоже ходит в API, только под капотом,
    # и в закрытое окно упрётся ровно так же — а его отказ выглядел бы как
    # сломанный токен.
    guard(REST, api_base())
    gh("auth", "status")

    # Пробы идут тем же путём, которым потом пойдёт горячий цикл, и условными
    # запросами: со второго раза они не стоят лимита.
    polled(
        f"repos/{home}/actions/workflows/{runner_workflow()}", "workflow", _reachable
    )
    polled(f"repos/{repo}", "доступ", _reachable)
    try:
        polled(f"repos/{repo}/actions/runners?per_page=1", "права", _reachable)
    except RateLimited:
        # Это не про права: ждать окно, а не переучивать пользователя.
        raise
    except NestedError:
        raise NestedError(
            f"нет прав администратора на {repo} — они нужны, чтобы завести scale set",
        ) from None

    _CHECKED[(repo, home)] = time.monotonic()


def registration_token(repo: str) -> str:
    payload = rest("POST", f"repos/{repo}/actions/runners/registration-token") or {}
    value = str(payload.get("token", ""))
    if not value:
        raise NestedError(f"пустой registration token для {repo}")
    return value


def _branch(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("default_branch", "")).strip()


def default_branch(repo: str) -> str:
    value = polled(f"repos/{repo}", "ветка", _branch)
    if not value:
        raise NestedError(f"не определил ветку по умолчанию для {repo}")
    return value


@dataclass(frozen=True)
class _Page:
    ids: tuple[int, ...]
    full: bool


def _title(run: dict[str, Any]) -> str:
    return str(run.get("display_title") or run.get("name") or "")


def _page(payload: Any, marker: str) -> _Page:
    runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
    ids = tuple(
        int(item["id"])
        for item in runs
        if isinstance(item, dict) and item.get("id") and _title(item).startswith(marker)
    )
    return _Page(ids, len(runs) >= RUNS_PER_PAGE)


def list_runs(home: str, target: str, status: str) -> list[int]:
    # Страницы обходим: в home-репо живут запуски всех целей, и обрезать
    # список до фильтра значит потерять свои же живые раннеры.
    marker = f"nested {target} "

    def pick(payload: Any) -> _Page:
        return _page(payload, marker)

    found: list[int] = []
    for page in range(1, RUNS_PAGES + 1):
        query = f"?status={status}&per_page={RUNS_PER_PAGE}&page={page}&exclude_pull_requests=true"
        got = polled(
            f"repos/{home}/actions/workflows/{runner_workflow()}/runs{query}",
            f"запуски {target}",
            pick,
        )
        found.extend(got.ids)
        if not got.full:
            break
    return found


def dispatch(home: str, target: str, jit: str, branch: str) -> bool:
    workflow = runner_workflow()
    try:
        rest(
            "POST",
            f"repos/{home}/actions/workflows/{workflow}/dispatches",
            body={"ref": branch, "inputs": {"target": target, "jit": jit}},
            attempts=DISPATCH_ATTEMPTS,
        )
    except RateLimited as exc:
        # Не сбой, а очередь за лимитом: раннер поедет, когда окно откроется.
        log.warning("диспатч %s отложен: %s", workflow, exc)
        return False
    except NestedError as exc:
        log.error("не удалось запустить %s: %s", workflow, exc)
        return False
    return True


def cancel_run(repo: str, run_id: int) -> bool:
    try:
        rest("POST", f"repos/{repo}/actions/runs/{run_id}/cancel")
    except HttpError as exc:
        if exc.status == _GONE:
            log.debug("run %s уже не бежит", run_id)
            return False
        log.warning("не отменил run %s: %s", run_id, exc)
        return False
    except NestedError as exc:
        log.warning("не отменил run %s: %s", run_id, exc)
        return False
    return True


def _ours(payload: Any) -> list[dict[str, Any]]:
    runners = payload.get("runners", []) if isinstance(payload, dict) else []
    return [
        item
        for item in runners
        if isinstance(item, dict)
        and str(item.get("name", "")).startswith(RUNNER_NAME_PREFIX)
    ]


def list_runners(repo: str) -> list[dict[str, Any]]:
    return polled(f"repos/{repo}/actions/runners?per_page=100", "раннеры", _ours)


def delete_runner(repo: str, runner_id: int) -> bool:
    try:
        rest("DELETE", f"repos/{repo}/actions/runners/{runner_id}")
    except NestedError:
        return False
    return True
