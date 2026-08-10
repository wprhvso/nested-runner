from __future__ import annotations

from dataclasses import dataclass
from typing import Any, override


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
    def parse(cls, raw: dict[str, Any] | None) -> Stats:
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

    @override
    def __str__(self) -> str:
        return (
            f"available={self.available} acquired={self.acquired} "
            f"assigned={self.assigned} running={self.running} "
            f"runners={self.registered} busy={self.busy} idle={self.idle}"
        )


@dataclass(frozen=True)
class Session:
    session_id: str
    queue_url: str
    queue_token: str
    queue_token_exp: float
    stats: Stats = Stats()
