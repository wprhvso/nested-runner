from __future__ import annotations

import functools
import logging
import os
import shutil
import subprocess
from typing import Any

from nested_runner.config import (
    DISPATCH_ATTEMPTS,
    GH_TIMEOUT,
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
from nested_runner.errors import HttpError, NestedError
from nested_runner.http import request

log = logging.getLogger("nested")

_INSTALL_HINT = "gh не найден — ставь: https://cli.github.com"
_REST_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": REST_VERSION,
}
_GONE = 409


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


def rest(method: str, path: str, *, body: object = None, attempts: int = 3) -> Any:
    return request(
        method,
        f"{api_base()}/{path.lstrip('/')}",
        auth=f"Bearer {token()}",
        body=body,
        attempts=attempts,
        extra=_REST_HEADERS,
    )


def current_repo() -> str:
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


def preflight(repo: str, home: str) -> None:
    if not REPO_PATTERN.fullmatch(repo):
        raise NestedError(f"ожидается owner/name, получено: {repo}")
    if not REPO_PATTERN.fullmatch(home):
        raise NestedError(f"GH_REPO должен быть owner/name, получено: {home}")
    if shutil.which("gh") is None:
        raise NestedError(_INSTALL_HINT)

    check_age()
    log.debug("публичный ключ: %s", recipient())

    gh("auth", "status")

    rest("GET", f"repos/{home}/actions/workflows/{runner_workflow()}")

    rest("GET", f"repos/{repo}")
    try:
        rest("GET", f"repos/{repo}/actions/runners?per_page=1")
    except NestedError:
        raise NestedError(
            f"нет прав администратора на {repo} — они нужны, чтобы завести scale set",
        ) from None


def registration_token(repo: str) -> str:
    payload = rest("POST", f"repos/{repo}/actions/runners/registration-token") or {}
    value = str(payload.get("token", ""))
    if not value:
        raise NestedError(f"пустой registration token для {repo}")
    return value


def default_branch(repo: str) -> str:
    payload = rest("GET", f"repos/{repo}") or {}
    value = str(payload.get("default_branch", "")).strip()
    if not value:
        raise NestedError(f"не определил ветку по умолчанию для {repo}")
    return value


def list_runs(home: str, target: str, status: str) -> list[dict[str, Any]]:
    marker = f"nested {target} "
    found: list[dict[str, Any]] = []
    for page in range(1, RUNS_PAGES + 1):
        query = f"?status={status}&per_page={RUNS_PER_PAGE}&page={page}&exclude_pull_requests=true"
        payload = rest(
            "GET", f"repos/{home}/actions/workflows/{runner_workflow()}/runs{query}"
        )
        runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
        found.extend(item for item in runs if _title(item).startswith(marker))
        if len(runs) < RUNS_PER_PAGE:
            break
    return found


def _title(run: dict[str, Any]) -> str:
    return str(run.get("display_title") or run.get("name") or "")


def dispatch(home: str, target: str, jit: str, branch: str) -> bool:
    workflow = runner_workflow()
    try:
        rest(
            "POST",
            f"repos/{home}/actions/workflows/{workflow}/dispatches",
            body={"ref": branch, "inputs": {"target": target, "jit": jit}},
            attempts=DISPATCH_ATTEMPTS,
        )
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


def list_runners(repo: str) -> list[dict[str, Any]]:
    payload = rest("GET", f"repos/{repo}/actions/runners?per_page=100") or {}
    runners = payload.get("runners", []) if isinstance(payload, dict) else []
    return [
        item
        for item in runners
        if str(item.get("name", "")).startswith(RUNNER_NAME_PREFIX)
    ]


def delete_runner(repo: str, runner_id: int) -> bool:
    try:
        rest("DELETE", f"repos/{repo}/actions/runners/{runner_id}")
    except NestedError:
        return False
    return True
