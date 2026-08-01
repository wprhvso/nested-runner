"""Thin GitHub REST API client."""

import logging
import time
from types import TracebackType
from typing import Any, Self, final

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from nested_runner.errors import NestedRunnerError

log = logging.getLogger(__name__)

API = "https://api.github.com"
WORKFLOW = "runner.yml"
MAX_SLEEP = 300


@final
class RetryableError(Exception):
    """Server-side hiccup or rate limit; worth another attempt."""


class Runner(BaseModel):
    """A self-hosted runner as GitHub reports it."""

    id: int
    name: str
    status: str
    busy: bool

    @property
    def available(self) -> bool:
        """Return True when this runner can pick up a job right now."""
        return self.status == "online" and not self.busy


@final
class GitHub:
    """Everything we ask of the GitHub API, and nothing else."""

    def __init__(self, token: str, timeout: float = 10.0) -> None:
        self._client: httpx.Client = httpx.Client(
            base_url=API,
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "nested-runner",
            },
        )
        self._default_branch: dict[str, str] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._client.close()

    # -- plumbing ---------------------------------------------------------

    @retry(
        retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, max=10),
        reraise=True,
    )
    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TransportError as error:
            log.debug("сеть подвела: %s", error)
            raise

        if response.is_success:
            return response

        raise self._error(response, path)

    def _error(self, response: httpx.Response, path: str) -> Exception:
        """Turn a failed response into the exception it deserves."""
        status = response.status_code
        message = self._message(response)

        if status >= 500:
            log.debug("github вернул %s, пробуем ещё раз", status)
            return RetryableError(message)

        if status == 401:
            return NestedRunnerError(
                "GitHub не принял токен",
                hint="протух или отозван — запусти: nested-runner login",
            )

        if status == 403:
            return self._error_403(response, message)

        if status == 404:
            return NestedRunnerError(
                f"не найдено: {path}",
                hint="проверь slug в конфиге, права токена и что runner.yml лежит в репозитории",
            )

        if status == 422:
            return NestedRunnerError(
                f"GitHub отказался: {message}",
                hint="проверь, что в runner.yml есть workflow_dispatch и ветка существует",
            )

        return NestedRunnerError(f"GitHub вернул {status}: {message}")

    def _error_403(self, response: httpx.Response, message: str) -> Exception:
        """Tell rate limits apart from missing permissions, waiting if asked to."""
        headers = response.headers

        retry_after = headers.get("retry-after")
        if retry_after:
            self._sleep(float(retry_after), "вторичный лимит")
            return RetryableError(message)

        if headers.get("x-ratelimit-remaining") == "0":
            reset = float(headers.get("x-ratelimit-reset", 0))
            self._sleep(reset - time.time(), "лимит запросов исчерпан")
            return RetryableError(message)

        hint = (
            "токену не хватает прав (Actions и Administration — Read and write), "
            "либо Actions выключены в настройках репозитория"
        )
        return NestedRunnerError(f"доступ запрещён: {message}", hint=hint)

    def _sleep(self, seconds: float, reason: str) -> None:
        delay = max(1.0, min(seconds, MAX_SLEEP))
        log.warning("%s, ждём %.0f сек", reason, delay)
        time.sleep(delay)

    def _message(self, response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text[:200]
        return str(payload.get("message", response.text[:200]))

    def _json(self, response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            raise NestedRunnerError("GitHub прислал не JSON") from None

    # -- api --------------------------------------------------------------

    def whoami(self) -> str:
        """Return the login of the authenticated user."""
        return str(self._json(self._request("GET", "/user"))["login"])

    def check_repo(self, slug: str) -> None:
        """Verify access to the repository, to runner.yml and to the runner list."""
        self._request("GET", f"/repos/{slug}")
        self._request("GET", f"/repos/{slug}/actions/workflows/{WORKFLOW}")
        self._request("GET", f"/repos/{slug}/actions/runners", params={"per_page": 1})

    def default_branch(self, slug: str) -> str:
        """Return the default branch, asking GitHub once per process."""
        if slug not in self._default_branch:
            payload = self._json(self._request("GET", f"/repos/{slug}"))
            self._default_branch[slug] = str(payload["default_branch"])
        return self._default_branch[slug]

    def list_runners(self, slug: str) -> list[Runner]:
        """Return the self-hosted runners currently registered on the repository."""
        payload = self._json(
            self._request("GET", f"/repos/{slug}/actions/runners", params={"per_page": 100})
        )
        try:
            return [Runner.model_validate(item) for item in payload.get("runners", [])]
        except ValidationError as error:
            raise NestedRunnerError(f"не разобрали ответ GitHub: {error}") from None

    def dispatch(self, slug: str) -> None:
        """Start one runner workflow run."""
        self._request(
            "POST",
            f"/repos/{slug}/actions/workflows/{WORKFLOW}/dispatches",
            json={"ref": self.default_branch(slug)},
        )
