from __future__ import annotations

from nested_runner.fleet import Fleet


def test_empty_fleet_is_not_worth_a_request() -> None:
    fleet = Fleet()
    assert fleet.tracking() is False


def test_a_dispatch_makes_the_sweep_worth_it() -> None:
    fleet = Fleet()
    fleet.born()
    assert fleet.tracking() is True

    fleet.observe({7})
    assert fleet.tracking() is True

    fleet.observe(set())
    assert fleet.tracking() is False


def test_a_dead_dispatch_stops_being_tracked(monkeypatch) -> None:
    from nested_runner import fleet as fleet_mod

    fleet = Fleet()
    fleet.born()
    monkeypatch.setattr(fleet_mod, "FLEET_TTL", -1.0)
    assert fleet.tracking() is False
