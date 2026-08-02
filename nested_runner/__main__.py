from __future__ import annotations

import logging
import sys
import threading
from typing import TYPE_CHECKING

from nested_runner.config import debug
from nested_runner.controller import install_stop_handler, run
from nested_runner.errors import NestedError

if TYPE_CHECKING:
    from collections.abc import Sequence

log = logging.getLogger("nested")

_USAGE = "usage: python3 -m nested_runner owner/name [owner/name ...]"
_EXIT_USAGE = 2
_EXIT_ERROR = 1
_EXIT_INTERRUPTED = 130


def _worker(repo: str, stop: threading.Event, results: dict[str, int]) -> None:
    try:
        results[repo] = run(repo, stop)
    except NestedError as exc:
        log.error("%s", exc)
        results[repo] = _EXIT_ERROR


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

    stop = install_stop_handler()
    results: dict[str, int] = {}
    threads = [
        threading.Thread(target=_worker, args=(repo, stop, results), name=repo) for repo in argv
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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
