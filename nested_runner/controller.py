from __future__ import annotations

import json
import logging
import signal
import threading
import time
from typing import TYPE_CHECKING

from nested_runner.api import ScaleSetApi
from nested_runner.config import (
    MAX_LOOP_FAILURES,
    SESSION_STATUSES,
    max_runners,
    scale_set_name,
)
from nested_runner.crypto import encrypt
from nested_runner.errors import HttpError, NestedError
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
from nested_runner.http import backoff

if TYPE_CHECKING:
    from types import FrameType

    from nested_runner.models import Session

log = logging.getLogger("nested")

_EXIT_INTERRUPTED = 130


def install_stop_handler() -> threading.Event:
    stop = threading.Event()

    def handler(signum: int, _frame: FrameType | None) -> None:
        if stop.is_set():
            log.warning("второй сигнал, выхожу немедленно")
            raise SystemExit(_EXIT_INTERRUPTED)
        log.info("сигнал %s — доработаю итерацию и уберу за собой", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, handler)
    return stop


def log_job_messages(raw_body: str) -> None:
    try:
        items = json.loads(raw_body or "[]")
    except json.JSONDecodeError:
        log.debug("не разобрал тело сообщения")
        return

    for item in items:
        log.info(
            "  %s: %s (run %s, request %s)",
            item.get("messageType"),
            item.get("jobDisplayName"),
            item.get("workflowRunId"),
            item.get("runnerRequestId"),
        )


def _drain_message(api: ScaleSetApi, session: Session) -> None:
    message = api.poll(session)
    if not message:
        return

    log_job_messages(message.get("body", ""))
    message_id = message.get("messageId")
    if message_id is None:
        return
    try:
        api.delete_message(session, message_id)
    except NestedError as exc:
        log.warning("не подтвердил сообщение %s: %s", message_id, exc)


def _acquire_jobs(api: ScaleSetApi, scale_set_id: int, limit: int) -> None:
    headroom = max(0, limit - api.statistics(scale_set_id).acquired)
    if headroom == 0:
        return

    for job in api.acquirable_jobs(scale_set_id)[:headroom]:
        request_id = job.get("runnerRequestId")
        if not request_id:
            continue
        try:
            api.acquire(scale_set_id, int(request_id))
            log.info("забрал job %s (%s)", request_id, job.get("jobDisplayName"))
        except NestedError as exc:
            log.warning("не забрал job %s: %s", request_id, exc)


def _pending(home: str, target: str) -> int:
    return sum(len(list_runs(home, target, state)) for state in ("queued", "in_progress"))


def _send_runner(
    api: ScaleSetApi,
    home: str,
    target: str,
    scale_set_id: int,
    branch: str,
) -> bool:
    try:
        jit = encrypt(api.generate_jit(scale_set_id))
    except NestedError as exc:
        log.warning("не подготовил JIT: %s", exc)
        return False
    return dispatch(home, target, jit, branch)


def _scale(  # noqa: PLR0913
    api: ScaleSetApi,
    home: str,
    target: str,
    scale_set_id: int,
    branch: str,
    limit: int,
) -> None:
    stats = api.statistics(scale_set_id)
    pending = _pending(home, target)

    need = max(0, stats.waiting - stats.idle - pending)
    room = max(0, limit - stats.registered - pending)

    sent = 0
    for _ in range(min(need, room)):
        if not _send_runner(api, home, target, scale_set_id, branch):
            break
        sent += 1

    log.info("%s | pending=%s need=%s dispatched=%s", stats, pending, need, sent)


def _cleanup(
    api: ScaleSetApi,
    home: str,
    target: str,
    scale_set_id: int,
    session: Session | None,
) -> None:
    if session is not None:
        try:
            api.close_session(scale_set_id, session)
        except NestedError as exc:
            log.warning("сессия не закрылась: %s", exc)

    cancelled = 0
    for state in ("in_progress", "queued"):
        for item in list_runs(home, target, state):
            if cancel_run(home, int(item["databaseId"])):
                cancelled += 1

    removed_set = False
    try:
        api.delete_scale_set(scale_set_id)
        removed_set = True
    except NestedError as exc:
        log.warning("scale set не удалён: %s", exc)

    removed = sum(delete_runner(target, int(item["id"])) for item in list_runners(target))

    log.info(
        "убрал за собой: cancelled=%s scale-set-removed=%s runners-removed=%s",
        cancelled,
        removed_set,
        removed,
    )


def run(repo: str, stop: threading.Event) -> int:
    home = current_repo()
    preflight(repo, home)

    limit = max_runners()
    name = scale_set_name()
    api = ScaleSetApi(repo)
    scale_set_id = int(api.ensure_scale_set(name)["id"])
    branch = default_branch(home)
    owner = f"nested-{threading.get_ident()}"

    log.info(
        "поехали: цель=%s раннеры=%s scale-set=%r id=%s max=%s ветка=%s",
        repo,
        home,
        name,
        scale_set_id,
        limit,
        branch,
    )
    log.info("остановка: Ctrl+C — раннеры и scale set будут снесены")

    session: Session | None = None
    failures = 0

    try:
        session = api.reopen_session(scale_set_id, None, owner)

        while not stop.is_set():
            try:
                _drain_message(api, session)
                if stop.is_set():
                    break

                _acquire_jobs(api, scale_set_id, limit)
                _scale(api, home, repo, scale_set_id, branch, limit)
                failures = 0

            except HttpError as exc:
                if exc.status not in SESSION_STATUSES:
                    raise
                log.info("сессия недействительна (%s), пересоздаю", exc.status)
                session = api.reopen_session(scale_set_id, session, owner)

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
                time.sleep(delay)
    finally:
        _cleanup(api, home, repo, scale_set_id, session)

    return 0
