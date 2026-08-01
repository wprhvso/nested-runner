from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

from nested_runner.config import debug
from nested_runner.controller import run
from nested_runner.errors import NestedError

if TYPE_CHECKING:
    from collections.abc import Sequence

log = logging.getLogger("nested")

_USAGE = "usage: python3 -m nested_runner owner/name"
_EXIT_USAGE = 2
_EXIT_ERROR = 1
_EXIT_INTERRUPTED = 130


def main(argv: Sequence[str]) -> int:
    logging.basicConfig(
        level=logging.DEBUG if debug() else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )

    if len(argv) != 1:
        sys.stderr.write(_USAGE + "\n")
        return _EXIT_USAGE

    try:
        return run(argv[0])
    except NestedError as exc:
        log.error("%s", exc)
        return _EXIT_ERROR
    except KeyboardInterrupt:
        return _EXIT_INTERRUPTED


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
