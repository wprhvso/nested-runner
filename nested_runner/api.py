from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any

from nested_runner.config import (
    API_VERSION,
    POLL_TIMEOUT,
    SESSION_CONFLICT_WAIT,
    TOKEN_SKEW,
    api_base,
    server_url,
)
from nested_runner.errors import HttpError, NestedError
from nested_runner.gh import registration_token
from nested_runner.http import jwt_expiry, request
from nested_runner.models import Session, Stats

log = logging.getLogger("nested")

_DEFAULT_RUNNER_GROUP = 1
_UNAUTHORIZED = 401
_NOT_FOUND = 404
_CONFLICT = 409


class ScaleSetApi:
    def __init__(self, repo: str) -> None:
        self.repo: str = repo
        self._pipeline_url: str | None = None
        self._token: str = ""
        self._token_exp: float = 0.0

    def _authenticate(self) -> None:
        info = request(
            "POST",
            f"{api_base()}/actions/runner-registration",
            auth=f"RemoteAuth {registration_token(self.repo)}",
            body={"url": f"{server_url()}/{self.repo}", "runnerEvent": "register"},
        )
        if not isinstance(info, dict) or "token" not in info or "url" not in info:
            raise NestedError("runner-registration не вернул token/url")

        self._token = info["token"]
        self._token_exp = jwt_expiry(self._token)
        self._pipeline_url = info["url"].rstrip("/")
        log.debug(
            "получен pipeline JWT, годен ещё %.0f мин",
            (self._token_exp - time.time()) / 60,
        )

    @property
    def token(self) -> str:
        if not self._token or time.time() > self._token_exp - TOKEN_SKEW:
            self._authenticate()
        return self._token

    @property
    def pipeline_url(self) -> str:
        if self._pipeline_url is None:
            self._authenticate()
        if self._pipeline_url is None:
            raise NestedError("не удалось получить pipeline URL")
        return self._pipeline_url

    def call(self, method: str, path: str, *, body: object = None, **kw: Any) -> Any:
        url = f"{self.pipeline_url}/_apis/runtime/{path}"
        url += ("&" if "?" in url else "?") + f"api-version={API_VERSION}"
        try:
            return request(method, url, auth=f"Bearer {self.token}", body=body, **kw)
        except HttpError as exc:
            if exc.status != _UNAUTHORIZED:
                raise
            log.info("pipeline JWT отвергнут, переполучаю")
            self._authenticate()
            return request(method, url, auth=f"Bearer {self.token}", body=body, **kw)

    def find_scale_set(self, name: str) -> dict[str, Any] | None:
        query = urllib.parse.quote(name, safe="")
        try:
            found = self.call("GET", f"runnerscalesets?name={query}")
        except HttpError as exc:
            if exc.status == _NOT_FOUND:
                return None
            raise

        values = found.get("value", []) if isinstance(found, dict) else (found or [])
        for item in values:
            if item.get("name") == name:
                return item
        return None

    def ensure_scale_set(self, name: str) -> dict[str, Any]:
        existing = self.find_scale_set(name)
        if existing:
            log.info("scale set %r уже есть, id=%s", name, existing["id"])
            return existing

        created = self.call(
            "POST",
            "runnerscalesets",
            body={
                "name": name,
                "runnerGroupId": _DEFAULT_RUNNER_GROUP,
                "runnerSetting": {"ephemeral": True, "disableUpdate": True},
            },
        )
        if not isinstance(created, dict) or "id" not in created:
            raise NestedError(f"не удалось создать scale set {name!r}")
        log.info("создал scale set %r, id=%s", name, created["id"])
        return created

    def delete_scale_set(self, scale_set_id: int) -> None:
        try:
            self.call("DELETE", f"runnerscalesets/{scale_set_id}")
        except HttpError as exc:
            if exc.status != _NOT_FOUND:
                raise
            log.debug("scale set %s уже удалён", scale_set_id)

    def statistics(self, scale_set_id: int) -> Stats:
        raw = self.call("GET", f"runnerscalesets/{scale_set_id}")
        payload = raw if isinstance(raw, dict) else {}
        return Stats.parse(payload.get("statistics"))

    def open_session(self, scale_set_id: int, owner: str) -> Session:
        raw = self.call(
            "POST",
            f"runnerscalesets/{scale_set_id}/sessions",
            body={"ownerName": owner},
        )
        if not isinstance(raw, dict) or "sessionId" not in raw:
            raise NestedError("не удалось открыть message session")

        token = raw["messageQueueAccessToken"]
        session = Session(
            session_id=raw["sessionId"],
            queue_url=raw["messageQueueUrl"],
            queue_token=token,
            queue_token_exp=jwt_expiry(token),
        )
        log.info("открыл message session %s", session.session_id)
        return session

    def close_session(self, scale_set_id: int, session: Session) -> None:
        try:
            self.call(
                "DELETE",
                f"runnerscalesets/{scale_set_id}/sessions/{session.session_id}",
            )
        except HttpError as exc:
            if exc.status != _NOT_FOUND:
                raise
            log.debug("сессия %s уже закрыта", session.session_id)
            return
        log.info("закрыл message session %s", session.session_id)

    def reopen_session(self, scale_set_id: int, session: Session | None, owner: str) -> Session:
        if session is not None:
            try:
                self.close_session(scale_set_id, session)
            except NestedError as exc:
                log.debug("старая сессия не закрылась: %s", exc)
        try:
            return self.open_session(scale_set_id, owner)
        except HttpError as exc:
            if exc.status != _CONFLICT:
                raise
            log.warning("на scale set висит активная сессия, жду %s с", SESSION_CONFLICT_WAIT)
            time.sleep(SESSION_CONFLICT_WAIT)
            return self.open_session(scale_set_id, owner)

    def poll(self, session: Session) -> dict[str, Any] | None:
        message = request(
            "GET",
            session.queue_url,
            auth=f"Bearer {session.queue_token}",
            timeout=POLL_TIMEOUT,
            attempts=2,
        )
        return message if isinstance(message, dict) else None

    def delete_message(self, session: Session, message_id: int) -> None:
        base, _, query = session.queue_url.partition("?")
        request(
            "DELETE",
            f"{base}/{message_id}?{query}",
            auth=f"Bearer {session.queue_token}",
            attempts=3,
        )

    def acquirable_jobs(self, scale_set_id: int) -> list[dict[str, Any]]:
        raw = self.call("GET", f"runnerscalesets/{scale_set_id}/acquirablejobs")
        if not raw:
            return []
        return raw.get("value", []) if isinstance(raw, dict) else raw

    def acquire(self, scale_set_id: int, request_id: int) -> None:
        self.call("POST", f"runnerscalesets/{scale_set_id}/jobs/{request_id}/acquire", body={})
