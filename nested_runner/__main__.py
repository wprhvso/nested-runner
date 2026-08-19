from __future__ import annotations

import logging
import sys
import threading
import time
from typing import TYPE_CHECKING

from nested_runner.budget import REST
from nested_runner.config import (
    RATE_WAIT_CAP,
    REPO_PATTERN,
    RESTART_CAP,
    RESTART_HEALTHY,
    debug,
)
from nested_runner.controller import install_stop_handler, run
from nested_runner.errors import NestedError
from nested_runner.http import backoff

if TYPE_CHECKING:
    from collections.abc import Sequence

log = logging.getLogger("nested")

_USAGE = "usage: nested-runner owner/name [owner/name ...]"
_EXIT_USAGE = 2
_EXIT_ERROR = 1
_EXIT_INTERRUPTED = 130


def _attempt(repo: str, stop: threading.Event, results: dict[str, int]) -> bool:
    try:
        results[repo] = run(repo, stop)
    except NestedError as exc:
        log.error("%s", exc)
    except Exception:
        log.exception("непредвиденный сбой")
    else:
        return True
    results[repo] = _EXIT_ERROR
    return False


def _worker(repo: str, stop: threading.Event, results: dict[str, int]) -> None:
    failures = 0
    while not stop.is_set():
        started = time.monotonic()
        if _attempt(repo, stop, results):
            return
        if time.monotonic() - started >= RESTART_HEALTHY:
            failures = 0
        failures += 1

        waiting = REST.shut()
        if waiting:
            delay = min(waiting, RATE_WAIT_CAP)
            log.warning(
                "лимит REST закрыт, старт через %.0f с — %s", delay, REST.state()
            )
        else:
            delay = backoff(failures, cap=RESTART_CAP)
            log.warning("перезапуск через %.1f с (сбой %s подряд)", delay, failures)
        if stop.wait(delay):
            return


def main(argv: Sequence[str]) -> int:
    logging.basicConfig(
        level=logging.DEBUG if debug() else logging.INFO,
        format="%(asctime)s %(levelname)-7s [%(threadName)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    if not argv:
        sys.stderr.write(_USAGE + "\n")
        return _EXIT_USAGE

    wrong = [repo for repo in argv if not REPO_PATTERN.fullmatch(repo)]
    if wrong:
        sys.stderr.write(
            f"ожидается owner/name, получено: {' '.join(wrong)}\n{_USAGE}\n"
        )
        return _EXIT_USAGE

    stop = install_stop_handler()
    results: dict[str, int] = {}
    threads = [
        threading.Thread(target=_worker, args=(repo, stop, results), name=repo)
        for repo in argv
    ]

    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    except SystemExit:
        stop.set()
        for thread in threads:
            thread.join()
        return _EXIT_INTERRUPTED

    if any(code != 0 for code in results.values()):
        return _EXIT_ERROR
    return 0


def cli() -> int:
    return main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(cli())
