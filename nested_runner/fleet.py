from __future__ import annotations

import threading
import time

from nested_runner.config import FLEET_TTL


class Fleet:
    """Сколько наших раннеров способны взять job прямо сейчас.

    Горячий путь читает только память — ни одного сетевого вызова между
    сообщением очереди и решением. Настоящий список живых запусков подвозит
    сверщик в фоне, плюс диспатчи, которых он ещё не увидел.
    """

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._alive: set[int] = set()
        self._unseen: list[float] = []
        self._retired: int = 0

    def born(self) -> None:
        with self._lock:
            self._unseen.append(time.monotonic())

    def retired(self, count: int = 1) -> None:
        # Раннер эфемерный: отработал job — больше ничего не возьмёт, значит
        # место свободно, не дожидаясь конца запуска.
        if count < 1:
            return
        with self._lock:
            self._retired += count
            self._clamp()

    def observe(self, alive: set[int]) -> None:
        with self._lock:
            # Появился новый запуск — это один из наших диспатчей доехал.
            # Списываем именно столько: запуск виден в API не сразу, и если
            # списать диспатч раньше времени, поедет второй раннер.
            del self._unseen[: len(alive - self._alive)]
            # Запуск исчез — значит отработавший раннер уже посчитан, снимаем.
            self._retired = max(0, self._retired - len(self._alive - alive))
            self._alive = alive
            self._forget()
            self._clamp()

    def size(self) -> int:
        with self._lock:
            self._forget()
            return max(0, len(self._alive) + len(self._unseen) - self._retired)

    def tracking(self) -> bool:
        """Есть ли что сверять.

        Запуски раннеров заводим только мы, так что пока флот пуст и ни одного
        диспатча в воздухе нет, список запусков в API измениться не может —
        и спрашивать его незачем. Один этот отказ снимает с лимита весь
        холостой ход контроллера.
        """
        with self._lock:
            self._forget()
            return bool(self._alive or self._unseen)

    def _forget(self) -> None:
        # Диспатч приняли, но запуска так и нет — считаем, что он не родился.
        stale = time.monotonic() - FLEET_TTL
        self._unseen = [at for at in self._unseen if at >= stale]

    def _clamp(self) -> None:
        self._retired = min(self._retired, len(self._alive) + len(self._unseen))
