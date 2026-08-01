"""Personal access token storage."""

import logging
import stat
from pathlib import Path

from nested_runner.errors import NestedRunnerError

log = logging.getLogger(__name__)


def token_path(directory: Path) -> Path:
    """Return the path of the token file."""
    return directory / "token"


def save(directory: Path, token: str) -> Path:
    """Write the token with owner-only permissions."""
    path = token_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.write_text(token.strip() + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def load(directory: Path) -> str:
    """Read the saved token, complaining usefully when there is none."""
    path = token_path(directory)

    try:
        token = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        raise NestedRunnerError(
            "токен не найден",
            hint="запусти: nested-runner login",
        ) from None
    except OSError as error:
        raise NestedRunnerError(f"токен не читается: {error}", hint=str(path)) from None

    if not token:
        raise NestedRunnerError(
            "файл с токеном пуст",
            hint="запусти: nested-runner login",
        )

    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        log.warning("права на %s — %o, стоило бы 600", path, mode)

    return token
