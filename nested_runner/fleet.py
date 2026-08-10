from __future__ import annotations

import threading
import time

from nested_runner.config import FLEET_TTL


class Fleet:
    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._alive: set[int] = set()
        self._unseen: list[float] = []
        self._retired: int = 0

    def born(self) -> None:
        with self._lock:
            self._unseen.append(time.monotonic())

    def retired(self, count: int = 1) -> None:
        if count < 1:
            return
        with self._lock:
            self._retired += count
            self._clamp()

    def observe(self, alive: set[int]) -> None:
        with self._lock:
            del self._unseen[: len(alive - self._alive)]
            self._retired = max(0, self._retired - len(self._alive - alive))
            self._alive = alive
            self._forget()
            self._clamp()

    def size(self) -> int:
        with self._lock:
            self._forget()
            return max(0, len(self._alive) + len(self._unseen) - self._retired)

    def _forget(self) -> None:
        stale = time.monotonic() - FLEET_TTL
        self._unseen = [at for at in self._unseen if at >= stale]

    def _clamp(self) -> None:
        self._retired = min(self._retired, len(self._alive) + len(self._unseen))
