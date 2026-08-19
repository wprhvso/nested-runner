from __future__ import annotations

from typing import Any

import pytest
from nested_runner import gh as gh_mod
from nested_runner.budget import Budget
from nested_runner.config import NOT_MODIFIED, RUNS_PER_PAGE
from nested_runner.http import Reply
from tests.conftest import Clock, headers


class Answers:
    def __init__(self, *replies: Reply) -> None:
        self.replies = list(replies)
        self.seen: list[tuple[str, str | None]] = []

    def __call__(self, _method: str, url: str, **kw: Any) -> Reply:
        self.seen.append((url, kw.get("etag")))
        return self.replies[min(len(self.seen) - 1, len(self.replies) - 1)]


@pytest.fixture(autouse=True)
def clean_store(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(gh_mod, "token", lambda: "тест")
    monkeypatch.setattr(gh_mod, "REST", Budget())
    gh_mod._STORE.clear()
    yield
    gh_mod._STORE.clear()


def _answer(monkeypatch: pytest.MonkeyPatch, *replies: Reply) -> Answers:
    made = Answers(*replies)
    monkeypatch.setattr(gh_mod, "fetch", made)
    return made


def _run(run_id: int, target: str) -> dict[str, Any]:
    return {"id": run_id, "display_title": f"nested {target} {run_id}"}


def test_304_serves_the_stored_result(monkeypatch: pytest.MonkeyPatch) -> None:
    made = _answer(
        monkeypatch,
        Reply(200, {"default_branch": "main"}, '"tag"'),
        Reply(NOT_MODIFIED, None, '"tag"'),
    )

    assert gh_mod.default_branch("owner/x") == "main"
    assert gh_mod.default_branch("owner/x") == "main"
    assert made.seen[1][1] == '"tag"'


def test_one_url_two_tags_do_not_collide(monkeypatch: pytest.MonkeyPatch) -> None:
    made = _answer(monkeypatch, Reply(200, {"default_branch": "main"}, '"tag"'))

    assert gh_mod.default_branch("owner/x") == "main"
    assert gh_mod.polled("repos/owner/x", "доступ", gh_mod._reachable) is None
    assert made.seen[1][1] is None


def test_answers_without_etag_are_not_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    made = _answer(monkeypatch, Reply(200, {"default_branch": "main"}, ""))

    assert gh_mod.default_branch("owner/x") == "main"
    assert gh_mod.default_branch("owner/x") == "main"
    assert made.seen[1][1] is None


def test_list_runs_keeps_only_its_own_target(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"workflow_runs": [_run(1, "owner/x"), _run(2, "owner/y"), {"id": 3}]}
    _answer(monkeypatch, Reply(200, payload, '"tag"'))

    assert gh_mod.list_runs("owner/home", "owner/x", "queued") == [1]
    assert gh_mod.list_runs("owner/home", "owner/y", "queued") == [2]


def test_list_runs_stops_on_a_short_page(monkeypatch: pytest.MonkeyPatch) -> None:
    full = {"workflow_runs": [_run(i, "owner/x") for i in range(RUNS_PER_PAGE)]}
    made = _answer(
        monkeypatch,
        Reply(200, full, '"1"'),
        Reply(200, {"workflow_runs": [_run(500, "owner/x")]}, '"2"'),
    )

    found = gh_mod.list_runs("owner/home", "owner/x", "in_progress")

    assert len(made.seen) == 2
    assert found[-1] == 500
    assert "page=2" in made.seen[1][0]


def test_preflight_is_not_repeated_on_every_restart(
    monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    monkeypatch.setattr(gh_mod, "gh", lambda *_a, **_kw: "")
    monkeypatch.setattr(gh_mod, "check_age", lambda: None)
    monkeypatch.setattr(gh_mod, "recipient", lambda: "age1тест")
    monkeypatch.setattr(gh_mod.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(gh_mod, "_CHECKED", {})
    made = _answer(monkeypatch, Reply(200, {}, '"tag"'))

    gh_mod.preflight("owner/x", "owner/home")
    spent = len(made.seen)
    gh_mod.preflight("owner/x", "owner/home")

    assert spent == 3
    assert len(made.seen) == spent


def test_rate_limit_is_not_reported_as_missing_rights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from nested_runner.errors import RateLimited

    monkeypatch.setattr(gh_mod, "gh", lambda *_a, **_kw: "")
    monkeypatch.setattr(gh_mod, "check_age", lambda: None)
    monkeypatch.setattr(gh_mod, "recipient", lambda: "age1тест")
    monkeypatch.setattr(gh_mod.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(gh_mod, "_CHECKED", {})

    def refuse(_method: str, url: str, **_kw: Any) -> Reply:
        if "runners" in url:
            raise RateLimited(403, url, "rate limit", 90.0)
        return Reply(200, {}, "")

    monkeypatch.setattr(gh_mod, "fetch", refuse)

    with pytest.raises(RateLimited):
        gh_mod.preflight("owner/x", "owner/home")


def test_closed_limit_short_circuits_preflight(
    monkeypatch: pytest.MonkeyPatch, clock: Clock
) -> None:
    from nested_runner.errors import RateLimited

    spawned: list[str] = []

    def spy(*args: str, **_kw: Any) -> str:
        spawned.append(args[0])
        return ""

    monkeypatch.setattr(gh_mod, "gh", spy)
    monkeypatch.setattr(gh_mod, "check_age", lambda: None)
    monkeypatch.setattr(gh_mod, "recipient", lambda: "age1тест")
    monkeypatch.setattr(gh_mod.shutil, "which", lambda _: "/usr/bin/gh")
    monkeypatch.setattr(gh_mod, "_CHECKED", {})
    made = _answer(monkeypatch, Reply(200, {}, '"tag"'))

    budget = Budget()
    budget.refuse(429, headers(Retry_After="60"), "rate limit")
    monkeypatch.setattr(gh_mod, "REST", budget)

    with pytest.raises(RateLimited):
        gh_mod.preflight("owner/x", "owner/home")

    assert spawned == []
    assert made.seen == []


def test_only_our_runners_come_back(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"runners": [{"id": 1, "name": "nested-a"}, {"id": 2, "name": "чужой"}]}
    _answer(monkeypatch, Reply(200, payload, '"tag"'))

    assert [item["id"] for item in gh_mod.list_runners("owner/x")] == [1]


def test_headers_helper_is_case_insensitive() -> None:
    budget = Budget()
    budget.observe(headers(x_ratelimit_remaining="7"))
    assert "остаток 7" in budget.state()
