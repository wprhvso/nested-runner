from __future__ import annotations

from typing import override

from nested_runner.redact import redact


class NestedError(Exception):
    pass


class HttpError(NestedError):
    def __init__(self, status: int, url: str, body: str) -> None:
        self.status: int = status
        self.url: str = url
        self.body: str = body[:500]
        super().__init__(f"HTTP {status} на {redact(url)}: {self.body}")


class RateLimited(HttpError):
    """Лимит GitHub выбран: до сброса окна в API ходить бесполезно.

    Отдельный тип затем, что лечится он не так, как остальные ошибки: повтор,
    пересоздание сессии и перезапуск контроллера тут только жгут остаток.
    """

    def __init__(self, status: int, url: str, body: str, retry_in: float) -> None:
        self.retry_in: float = retry_in
        super().__init__(status, url, body)

    @override
    def __str__(self) -> str:
        return f"{super().__str__()} — ждать {self.retry_in:.0f} с"
