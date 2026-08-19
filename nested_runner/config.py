from __future__ import annotations

import os
import re
from pathlib import Path

from nested_runner.errors import NestedError

API_VERSION = "6.0-preview"
REST_VERSION = "2022-11-28"

USER_AGENT = "nested-runner/0.2.0"

POLL_TIMEOUT = 90
REQUEST_TIMEOUT = 30
GH_TIMEOUT = 60

TOKEN_SKEW = 300
SESSION_CONFLICT_WAIT = 30

MAX_ATTEMPTS = 5
MAX_LOOP_FAILURES = 5
BACKOFF_BASE = 1.5
BACKOFF_CAP = 30.0
RESTART_CAP = 60.0
RESTART_HEALTHY = 300.0

QUEUE_MESSAGE_TYPE = "RunnerScaleSetJobMessages"
JOB_AVAILABLE = "JobAvailable"
JOB_COMPLETED = "JobCompleted"
CAPACITY_HEADER = "X-ScaleSetMaxCapacity"

DISPATCH_WORKERS = 8
DISPATCH_ATTEMPTS = 1

FLEET_INTERVAL = 15.0
FLEET_TTL = 120.0
RUN_STATUSES = ("queued", "in_progress")
RUNS_PER_PAGE = 100
RUNS_PAGES = 3

RUNNER_NAME_PREFIX = "nested-"
PUBLIC_KEY_PATH = Path("keys/nested.pub")
DEFAULT_HOME_REPO = "wprhvso/nested-runner"

RETRY_STATUSES = frozenset({408, 429, 500, 502, 503, 504})
SESSION_STATUSES = frozenset({401, 404, 409})
NOT_MODIFIED = 304

RATE_STATUSES = frozenset({403, 429})
RATE_RESERVE = 200
RATE_SHARE = 0.5
RATE_BLIND_WAIT = 60.0
RATE_WAIT_CAP = 300.0
RATE_WINDOW = 3600.0

PREFLIGHT_TTL = 600.0

REPO_PATTERN = re.compile(r"[^/\s]+/[^/\s]+")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise NestedError(f"{name} должен быть числом, получено: {raw}") from None
    if value < 1:
        raise NestedError(f"{name} должен быть больше нуля, получено: {value}")
    return value


def home_repo() -> str:
    return os.environ.get("GH_REPO", "").strip() or DEFAULT_HOME_REPO


def home_repo_configured() -> bool:
    return bool(os.environ.get("GH_REPO", "").strip())


def scale_set_name() -> str:
    return os.environ.get("NESTED_SCALE_SET", "nested")


def max_runners() -> int:
    return _env_int("NESTED_MAX", 20)


def runner_workflow() -> str:
    return os.environ.get("NESTED_WORKFLOW", "runner.yml")


def public_key_path() -> Path:
    return PUBLIC_KEY_PATH


def api_base() -> str:
    return os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")


def server_url() -> str:
    return os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")


def debug() -> bool:
    return bool(os.environ.get("NESTED_DEBUG"))
