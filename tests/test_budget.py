from __future__ import annotations

from nested_runner.budget import Budget
from nested_runner.config import RATE_BLIND_WAIT, RATE_WINDOW
from tests.conftest import Clock, headers


def _fresh(clock: Clock, left: float, window: float = 3600.0) -> Budget:
    budget = Budget()
    budget.observe(
        headers(
            X_RateLimit_Remaining=str(left),
            X_RateLimit_Reset=str(clock.wall + window),
        )
    )
    return budget


def test_plain_403_is_not_a_limit(clock: Clock) -> None:
    # Прав не хватает — ждать нечего и нельзя: иначе кривой токен молча
    # превращается в вечную паузу.
    budget = _fresh(clock, 4999)
    body = "Resource not accessible by personal access token"
    assert budget.refuse(403, headers(X_RateLimit_Remaining="4999"), body) == 0.0
    assert budget.shut() == 0.0


def test_primary_limit_closes_until_reset(clock: Clock) -> None:
    budget = _fresh(clock, 0)
    waiting = budget.refuse(
        403,
        headers(
            X_RateLimit_Remaining="0",
            X_RateLimit_Reset=str(clock.wall + 120),
        ),
        "API rate limit exceeded for user ID 177990150.",
    )
    assert waiting == 120
    assert budget.shut() == 120

    clock.tick(119)
    assert budget.shut() == 1
    clock.tick(1)
    assert budget.shut() == 0.0


def test_retry_after_wins(clock: Clock) -> None:
    budget = _fresh(clock, 500)
    assert budget.refuse(429, headers(Retry_After="42"), "slow down") == 42


def test_secondary_limit_without_headers(clock: Clock) -> None:
    # Вторичный лимит умеет приходить вообще без счётчиков — остаётся текст.
    budget = _fresh(clock, 4000)
    body = "You have exceeded a secondary rate limit."
    assert budget.refuse(403, headers(), body) == RATE_BLIND_WAIT


def test_absurd_wait_is_clamped_to_a_window(clock: Clock) -> None:
    budget = _fresh(clock, 0)
    assert budget.refuse(429, headers(Retry_After="999999"), "") == RATE_WINDOW


def test_success_reopens_the_gate(clock: Clock) -> None:
    budget = _fresh(clock, 0)
    budget.refuse(429, headers(Retry_After="300"), "")
    assert budget.shut() == 300

    budget.observe(headers(X_RateLimit_Remaining="4999"))
    assert budget.shut() == 0.0


def test_pace_stretches_as_the_limit_drains(clock: Clock) -> None:
    # 4800 остатка, половина фону, окно час — два запроса раз в три секунды.
    budget = _fresh(clock, 5000)
    assert budget.spend("сверка", 2) is True
    assert budget.spend("сверка", 2) is False
    clock.tick(3)
    assert budget.spend("сверка", 2) is True

    # Остатка меньше — интервал длиннее, и это единственный рычаг: сама
    # частота опроса в контроллере не меняется.
    thin = _fresh(clock, 1000)
    assert thin.spend("сверка", 2) is True
    clock.tick(3)
    assert thin.spend("сверка", 2) is False
    clock.tick(15)
    assert thin.spend("сверка", 2) is True


def test_reserve_stops_background_polling(clock: Clock) -> None:
    budget = _fresh(clock, 150, window=600.0)
    assert budget.spend("сверка", 2) is True
    clock.tick(599)
    assert budget.spend("сверка", 2) is False
    clock.tick(1)
    assert budget.spend("сверка", 2) is True


def test_lanes_share_one_limit(clock: Clock) -> None:
    # Токен один на все цели, значит и доля фона делится между ними.
    budget = _fresh(clock, 5000)
    assert budget.spend("a", 2) is True
    assert budget.spend("b", 2) is True

    clock.tick(3)
    assert budget.spend("b", 2) is False
    clock.tick(3)
    assert budget.spend("b", 2) is True


def test_closed_gate_blocks_every_lane(clock: Clock) -> None:
    budget = _fresh(clock, 5000)
    budget.refuse(429, headers(Retry_After="60"), "")
    assert budget.spend("a") is False
    assert budget.spend("b") is False
    clock.tick(60)
    assert budget.spend("a") is True


def test_unknown_limit_does_not_block(clock: Clock) -> None:
    # Ни одного ответа ещё не видели — мешать нечему.
    budget = Budget()
    assert budget.spend("сверка", 2) is True
    assert budget.spend("сверка", 2) is True
