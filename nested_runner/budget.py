from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from nested_runner.config import (
    RATE_BLIND_WAIT,
    RATE_RESERVE,
    RATE_SHARE,
    RATE_STATUSES,
    RATE_WINDOW,
)

if TYPE_CHECKING:
    from email.message import Message

# Вторичный лимит приходит без счётчиков в заголовках — остаётся текст.
_MARKERS = ("rate limit", "abuse detection")


def _number(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class Budget:
    """Остаток REST-лимита GitHub — один на процесс, как и токен.

    Знает ровно то, что GitHub сам пишет в заголовках, и отвечает на два
    вопроса: можно ли сейчас тратить лимит вообще и как редко ходить фоном,
    чтобы к концу окна осталось на диспатч и уборку.
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._left: float | None = None
        # Оба срока — в monotonic: сброс лимита GitHub присылает в epoch, но
        # решать по системным часам нельзя, их могут перевести.
        self._reset: float = 0.0
        self._closed: float = 0.0
        self._lanes: dict[str, float] = {}

    def observe(self, headers: Message) -> None:
        """Запомнить остаток. Заголовки есть в любом ответе, не только в 200."""
        left = _number(headers.get("X-RateLimit-Remaining"))
        if left is None:
            return
        reset = _number(headers.get("X-RateLimit-Reset"))
        now = time.monotonic()
        with self._lock:
            self._left = left
            if reset is not None:
                self._reset = now + max(0.0, reset - time.time())
            if left > 0:
                self._closed = min(self._closed, now)

    def refuse(self, status: int, headers: Message, body: str) -> float:
        """Сколько ждать, если ответ — упёршийся лимит. Ноль — дело не в нём."""
        if status not in RATE_STATUSES:
            return 0.0

        wait = _number(headers.get("Retry-After"))
        if wait is None:
            left = _number(headers.get("X-RateLimit-Remaining"))
            reset = _number(headers.get("X-RateLimit-Reset"))
            if left == 0 and reset is not None:
                wait = reset - time.time()
            elif any(mark in body.lower() for mark in _MARKERS):
                wait = RATE_BLIND_WAIT
        if wait is None:
            # Обычный 403: не хватает прав. Ждать бесполезно, и ждать нельзя —
            # иначе кривой токен молча превращается в вечную паузу.
            return 0.0

        wait = min(max(wait, 1.0), RATE_WINDOW)
        now = time.monotonic()
        with self._lock:
            self._left = 0.0
            self._closed = max(self._closed, now + wait)
            return self._closed - now

    def shut(self) -> float:
        """Сколько секунд лимит ещё закрыт. Ноль — можно тратить."""
        with self._lock:
            return max(0.0, self._closed - time.monotonic())

    def spend(self, lane: str, cost: int = 1) -> bool:
        """Пустить ли фоновую дорожку в API прямо сейчас."""
        now = time.monotonic()
        with self._lock:
            # setdefault, а не get: дорожка должна попасть в делитель доли
            # уже на первом своём вопросе, иначе первая цель забирает всё.
            due = self._lanes.setdefault(lane, 0.0)
            if now < self._closed or now < due:
                return False
            self._lanes[lane] = now + self._pace(cost, now)
            return True

    def _pace(self, cost: int, now: float) -> float:
        """Интервал дорожки: её доля остатка, растянутая до конца окна."""
        if self._left is None:
            # Ни одного ответа ещё не видели — мерить нечем, не мешаем.
            return 0.0
        window = max(0.0, self._reset - now)
        share = (self._left - RATE_RESERVE) * RATE_SHARE / len(self._lanes)
        if share < cost:
            # Что осталось — только горячему пути. Фон ждёт нового окна.
            return window
        return window * cost / share

    def state(self) -> str:
        with self._lock:
            left = "?" if self._left is None else f"{self._left:.0f}"
            window = max(0.0, self._reset - time.monotonic())
        return f"остаток {left}, окно ещё {window / 60:.0f} мин"


# Токен один, репозиториев может быть несколько — бюджет общий.
REST = Budget()
