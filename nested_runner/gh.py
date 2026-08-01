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
    run_title,
    runner_workflow,
)
from nested_runner.errors import NestedError

log = logging.getLogger("nested")

_INSTALL_HINT = "gh не найден — ставь: https://cli.github.com"
_HOME_HINT = "запускать из каталога репозитория nested-runner (нужен git remote)"
_SECRET_HINT = (
    "нет секрета RUNNER_PAT\n"  # noqa: S105
    "сделай fine-grained PAT на целевой репозиторий (Administration rw, Contents r) и:\n"
    "  just secret <token>"
)


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
    try:
        out = gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner").strip()
    except NestedError:
        raise NestedError(_HOME_HINT) from None
    if not out:
        raise NestedError(_HOME_HINT)
    return out


def has_secret(repo: str) -> bool:
    names = gh_json("secret", "list", "--repo", repo, "--json", "name", default=[])
    return any(item.get("name") == "RUNNER_PAT" for item in names)


def preflight(repo: str, home: str) -> None:
    if not REPO_PATTERN.fullmatch(repo):
        raise NestedError(f"ожидается owner/name, получено: {repo}")
    if shutil.which("gh") is None:
        raise NestedError(_INSTALL_HINT)

    gh("auth", "status")

    gh("api", f"repos/{home}")
    gh("api", f"repos/{home}/actions/workflows/{runner_workflow()}")
    if not has_secret(home):
        raise NestedError(_SECRET_HINT)

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


def list_runs(home: str, repo: str, status: str) -> list[dict[str, Any]]:
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
    title = run_title(repo)
    return [item for item in runs if item.get("displayTitle") == title]


def dispatch(home: str, repo: str, scale_set_id: int, branch: str) -> bool:
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
            f"scale_set_id={scale_set_id}",
            "-f",
            f"target_repo={repo}",
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
