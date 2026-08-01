from __future__ import annotations

import base64
import binascii
import json
import logging
import random
import time
import urllib.error
import urllib.request
from typing import Any

from nested_runner.config import (
    BACKOFF_BASE,
    BACKOFF_CAP,
    MAX_ATTEMPTS,
    REQUEST_TIMEOUT,
    RETRY_STATUSES,
    USER_AGENT,
)
from nested_runner.errors import HttpError, NestedError
from nested_runner.redact import redact

log = logging.getLogger("nested")

_FALLBACK_TTL = 1800.0


def backoff(attempt: int, retry_after: str | None = None) -> float:
    if retry_after:
        try:
            return min(float(retry_after), BACKOFF_CAP)
        except ValueError:
            pass
    return min(BACKOFF_BASE**attempt, BACKOFF_CAP) * (0.5 + random.random())


def request(  #  noqa: PLR0913
    method: str,
    url: str,
    *,
    auth: str | None = None,
    body: object = None,
    timeout: int = REQUEST_TIMEOUT,
    attempts: int = MAX_ATTEMPTS,
) -> Any:
    payload = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if auth:
        headers["Authorization"] = auth

    last: Exception | None = None
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            if exc.code not in RETRY_STATUSES:
                raise HttpError(exc.code, url, detail) from None
            last = HttpError(exc.code, url, detail)
            delay = backoff(attempt, exc.headers.get("Retry-After"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            delay = backoff(attempt)

        if attempt + 1 < attempts:
            log.debug("повтор %s %s через %.1f с (%s)", method, redact(url), delay, last)
            time.sleep(delay)

    raise NestedError(f"{method} {redact(url)} не удался за {attempts} попыток: {last}")


def jwt_expiry(token: str) -> float:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return float(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError, binascii.Error):
        log.debug("не разобрал exp из токена, считаю его коротким")
        return time.time() + _FALLBACK_TTL
