from __future__ import annotations

import json
import logging
import shutil
import subprocess
from typing import Any

from nested_runner.config import (
    GH_TIMEOUT,
    REPO_PATTERN,
    RUNNER_NAME_PREFIX,
    home_repo,
    home_repo_configured,
    runner_workflow,
)
from nested_runner.crypto import check_age, recipient
from nested_runner.errors import NestedError

log = logging.getLogger("nested")

_INSTALL_HINT = "gh не найден — ставь: https://cli.github.com"


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


def current_repo() -> str:
    fallback = home_repo()
    if home_repo_configured():
        return fallback
    try:
        out = gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").strip()
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

    gh("api", f"repos/{home}/actions/workflows/{runner_workflow()}")

    gh("api", f"repos/{repo}")
    try:
        gh("api", f"repos/{repo}/actions/runners?per_page=1")
    except NestedError:
        raise NestedError(
            f"нет прав администратора на {repo} — они нужны, чтобы завести scale set",
        ) from None


def registration_token(repo: str) -> str:
    return gh(
        "api",
        "-X",
        "POST",
        f"repos/{repo}/actions/runners/registration-token",
        "--jq",
        ".token",
    ).strip()


def default_branch(repo: str) -> str:
    return gh("api", f"repos/{repo}", "--jq", ".default_branch").strip()


def list_runs(home: str, target: str, status: str) -> list[dict[str, Any]]:
    runs = gh_json(
        "run",
        "list",
        "--repo",
        home,
        "--workflow",
        runner_workflow(),
        "--status",
        status,
        "--limit",
        "100",
        "--json",
        "databaseId,displayTitle",
        default=[],
    )
    marker = f"nested {target} "
    return [item for item in runs if str(item.get("displayTitle", "")).startswith(marker)]


def dispatch(home: str, target: str, jit: str, branch: str) -> bool:
    workflow = runner_workflow()
    try:
        gh(
            "workflow",
            "run",
            workflow,
            "--repo",
            home,
            "--ref",
            branch,
            "-f",
            f"target={target}",
            "-f",
            f"jit={jit}",
        )
    except NestedError as exc:
        log.error("не удалось запустить %s: %s", workflow, exc)
        return False
    return True


def cancel_run(repo: str, run_id: int) -> bool:
    try:
        gh("run", "cancel", str(run_id), "--repo", repo)
    except NestedError as exc:
        log.warning("не отменил run %s: %s", run_id, exc)
        return False
    return True


def list_runners(repo: str) -> list[dict[str, Any]]:
    payload = gh_json("api", f"repos/{repo}/actions/runners", default={}) or {}
    runners = payload.get("runners", [])
    return [item for item in runners if str(item.get("name", "")).startswith(RUNNER_NAME_PREFIX)]


def delete_runner(repo: str, runner_id: int) -> bool:
    try:
        gh("api", "-X", "DELETE", f"repos/{repo}/actions/runners/{runner_id}")
    except NestedError:
        return False
    return True
