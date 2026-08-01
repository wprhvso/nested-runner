"""Configuration file loading and validation."""

import tomllib
from pathlib import Path

import platformdirs
from pydantic import BaseModel, Field, ValidationError, field_validator

from nested_runner.errors import NestedRunnerError

APP_NAME = "nested-runner"

DEFAULT_CONFIG = """\
poll_seconds = 10

# Add one block per repository:
#
# [[repos]]
# slug = "owner/name"
# warm = 2
"""


def config_dir() -> Path:
    """Return the platform-specific configuration directory."""
    return Path(platformdirs.user_config_dir(APP_NAME))


def config_path(directory: Path) -> Path:
    """Return the path of the configuration file."""
    return directory / "config.toml"


class RepoConfig(BaseModel):
    """A single repository to keep runners for."""

    slug: str
    warm: int = Field(default=2, gt=0)

    @field_validator("slug")
    @classmethod
    def _check_slug(cls, value: str) -> str:
        parts = value.split("/")
        if len(parts) != 2 or not all(parts):
            msg = f"ожидается owner/name, получено {value!r}"
            raise ValueError(msg)
        return value

    @property
    def owner(self) -> str:
        """Return the owner part of the slug."""
        return self.slug.split("/")[0]

    @property
    def name(self) -> str:
        """Return the repository part of the slug."""
        return self.slug.split("/")[1]


class Config(BaseModel):
    """Everything the controller needs to run."""

    poll_seconds: int = Field(default=10, ge=1)
    repos: list[RepoConfig] = Field(default_factory=list)

    @field_validator("repos")
    @classmethod
    def _check_duplicates(cls, value: list[RepoConfig]) -> list[RepoConfig]:
        seen = {repo.slug for repo in value}
        if len(seen) != len(value):
            msg = "один и тот же репозиторий указан дважды"
            raise ValueError(msg)
        return value


def write_default(directory: Path) -> Path:
    """Create a configuration file with nothing but poll_seconds in it."""
    path = config_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return path


def load(directory: Path) -> Config:
    """Read and validate the configuration file."""
    path = config_path(directory)

    if not path.exists():
        write_default(directory)
        raise NestedRunnerError(
            "конфига не было, создали новый",
            hint=f"{path}\nдобавь туда блок [[repos]] со своим репозиторием",
        )

    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise NestedRunnerError(f"конфиг не читается: {error}", hint=str(path)) from None
    except OSError as error:
        raise NestedRunnerError(f"конфиг не открывается: {error}", hint=str(path)) from None

    try:
        config = Config.model_validate(raw)
    except ValidationError as error:
        problems = "\n".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'config'}: {item['msg']}"
            for item in error.errors()
        )
        raise NestedRunnerError(f"конфиг заполнен неверно:\n{problems}", hint=str(path)) from None

    if not config.repos:
        raise NestedRunnerError(
            "ай-ай-ай, репозиториев-то нету вовсе",
            hint=f'{path}\nдобавь блок:\n\n[[repos]]\nslug = "owner/name"\nwarm = 2',
        )

    return config
