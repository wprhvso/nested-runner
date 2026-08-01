"""The single error type used across the project."""

from typing import final, override


@final
class NestedRunnerError(Exception):
    """An error we can explain to the user.

    Anything raised as NestedRunnerError is printed as a friendly panel and
    exits with code 1. Everything else escapes as a traceback, because it is
    a bug.
    """

    def __init__(self, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.message: str = message
        self.hint: str | None = hint

    @override
    def __str__(self) -> str:
        return f"{self.message}\n{self.hint}" if self.hint else self.message
