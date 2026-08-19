from __future__ import annotations

import base64
import binascii
import http.client
import json
import logging
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nested_runner.config import (
    BACKOFF_BASE,
    BACKOFF_CAP,
    MAX_ATTEMPTS,
    NOT_MODIFIED,
    REQUEST_TIMEOUT,
    RETRY_STATUSES,
    USER_AGENT,
)
from nested_runner.errors import HttpError, NestedError, RateLimited
from nested_runner.redact import redact

if TYPE_CHECKING:
    from nested_runner.budget import Budget

log = logging.getLogger("nested")

_FALLBACK_TTL = 1800.0
_TOO_MANY = 429

STOP = threading.Event()


def pause(seconds: float) -> bool:
    if seconds <= 0:
        return STOP.is_set()
    return STOP.wait(seconds)


def guard(budget: Budget, url: str) -> None:
    waiting = budget.shut()
    if waiting:
        raise RateLimited(_TOO_MANY, url, "лимит ещё не сбросился", waiting)


def backoff(
    attempt: int, retry_after: str | None = None, cap: float = BACKOFF_CAP
) -> float:
    if retry_after:
        try:
            return min(float(retry_after), cap)
        except ValueError:
            pass
    return min(BACKOFF_BASE**attempt, cap) * (0.5 + random.random())


@dataclass(frozen=True)
class Reply:
    status: int
    payload: Any
    etag: str


def fetch(
    method: str,
    url: str,
    *,
    auth: str | None = None,
    body: object = None,
    timeout: int = REQUEST_TIMEOUT,
    attempts: int = MAX_ATTEMPTS,
    extra: dict[str, str] | None = None,
    etag: str | None = None,
    budget: Budget | None = None,
) -> Reply:
    payload = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if extra:
        headers.update(extra)
    if auth:
        headers["Authorization"] = auth
    if etag:
        headers["If-None-Match"] = etag

    if budget is not None:
        guard(budget, url)

    last: Exception | None = None
    hurried = False
    for attempt in range(attempts):
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)  # noqa: S310
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                if budget is not None:
                    budget.observe(resp.headers)
                raw = resp.read()
                return Reply(
                    resp.status,
                    json.loads(raw) if raw else None,
                    str(resp.headers.get("ETag") or ""),
                )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            if budget is not None:
                budget.observe(exc.headers)
            if exc.code == NOT_MODIFIED:
                return Reply(NOT_MODIFIED, None, etag or "")

            waiting = 0.0
            if budget is not None:
                waiting = budget.refuse(exc.code, exc.headers, detail)
            if waiting:
                raise RateLimited(exc.code, url, detail, waiting) from None
            if exc.code not in RETRY_STATUSES:
                raise HttpError(exc.code, url, detail) from None
            last = HttpError(exc.code, url, detail)
            delay = backoff(attempt, exc.headers.get("Retry-After"))
        except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
            last = exc
            delay = backoff(attempt)

        if attempt + 1 >= attempts:
            break
        log.debug("повтор %s %s через %.1f с (%s)", method, redact(url), delay, last)
        stopping = pause(delay)
        if stopping and hurried:
            break
        hurried = hurried or stopping

    raise NestedError(f"{method} {redact(url)} не удался за {attempts} попыток: {last}")


def request(
    method: str,
    url: str,
    *,
    auth: str | None = None,
    body: object = None,
    timeout: int = REQUEST_TIMEOUT,
    attempts: int = MAX_ATTEMPTS,
    extra: dict[str, str] | None = None,
    budget: Budget | None = None,
) -> Any:
    return fetch(
        method,
        url,
        auth=auth,
        body=body,
        timeout=timeout,
        attempts=attempts,
        extra=extra,
        budget=budget,
    ).payload


def jwt_expiry(token: str) -> float:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return float(claims["exp"])
    except (IndexError, KeyError, TypeError, ValueError, binascii.Error):
        log.debug("не разобрал exp из токена, считаю его коротким")
        return time.time() + _FALLBACK_TTL
