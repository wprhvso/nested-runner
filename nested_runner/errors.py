from __future__ import annotations

from nested_runner.redact import redact


class NestedError(Exception):
    pass


class HttpError(NestedError):
    def __init__(self, status: int, url: str, body: str) -> None:
        self.status: int = status
        self.url: str = url
        self.body: str = body[:500]
        super().__init__(f"HTTP {status} на {redact(url)}: {self.body}")
