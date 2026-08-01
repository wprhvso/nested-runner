"""The polling loop: keep a warm pool of idle runners."""

import logging
import time
from dataclasses import dataclass
from typing import final

from nested_runner.config import Config, RepoConfig
from nested_runner.errors import NestedRunnerError
from nested_runner.github import GitHub

log = logging.getLogger(__name__)

INFLIGHT_TTL = 90.0
"""Seconds a dispatched runner may take to show up in the API."""


@final
@dataclass(frozen=True)
class Plan:
    """What one repository looks like right now."""

    online: int
    idle: int
    inflight: int
    need: int


@final
class Scheduler:
    """Keeps every configured repository stocked with idle runners."""

    def __init__(self, github: GitHub) -> None:
        self._github: GitHub = github
        self._inflight: dict[str, list[float]] = {}

    def plan(self, repo: RepoConfig) -> Plan:
        """Count what exists and what is on the way. No side effects."""
        runners = self._github.list_runners(repo.slug)
        online = sum(1 for runner in runners if runner.status == "online")
        idle = sum(1 for runner in runners if runner.available)
        inflight = len(self._live(repo.slug))

        need = max(0, repo.warm - idle - inflight)
        return Plan(online=online, idle=idle, inflight=inflight, need=need)

    def apply(self, repo: RepoConfig, need: int) -> int:
        """Dispatch runners, remembering each one until it registers."""
        sent = 0
        for _ in range(need):
            self._github.dispatch(repo.slug)
            self._inflight.setdefault(repo.slug, []).append(time.monotonic())
            sent += 1
        return sent

    def tick(self, repo: RepoConfig) -> None:
        """Do one round for one repository."""
        state = self.plan(repo)
        sent = self.apply(repo, state.need)
        log.info(
            "%s online=%d idle=%d inflight=%d need=%d dispatched=%d",
            repo.slug,
            state.online,
            state.idle,
            state.inflight,
            state.need,
            sent,
        )

    def serve(self, config: Config) -> None:
        """Loop until interrupted. One bad repo must not stop the rest."""
        log.info("поехали: %d репо, тик %d сек", len(config.repos), config.poll_seconds)
        while True:
            for repo in config.repos:
                try:
                    self.tick(repo)
                except NestedRunnerError as error:
                    log.error("%s: %s", repo.slug, error.message)  # noqa: TRY400
                except Exception:
                    log.exception("%s: неожиданная ошибка", repo.slug)
            time.sleep(config.poll_seconds)

    def _live(self, slug: str) -> list[float]:
        """Drop dispatches old enough to have registered or died."""
        now = time.monotonic()
        live = [stamp for stamp in self._inflight.get(slug, []) if now - stamp < INFLIGHT_TTL]
        self._inflight[slug] = live
        return live
