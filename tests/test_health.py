from __future__ import annotations

from datetime import date

from conftest import at, make_harness
from muninn.adapters.sources.health import parse_health, sleep_hours
from muninn.core.metrics import default_metrics


def test_sleep_units_by_size():
    assert sleep_hours(25200) == 7 and sleep_hours(420) == 7 and sleep_hours(7) == 7


def test_steps_and_sleep_become_observations():
    now = at(2026, 10, 21, 11)
    obs = parse_health({"steps": "5,321", "sleep": 25920}, now, date(2026, 10, 21))
    assert {o.kind: o.payload for o in obs} == {"steps": {"steps": 5321, "day": "2026-10-21"},
                                                "sleep": {"hours": 7.2, "day": "2026-10-21"}}
    assert parse_health({"sleep": 99999}, now, date(2026, 10, 21)) == []  # 27 h: a bad export


def test_reported_sleep_beats_the_estimate_and_a_later_report_corrects():
    h = make_harness(at(2026, 10, 21, 11))
    plan = next(p for p in h.engine.plans.load() if p.id == "health")
    goal = plan.goals[0]
    metric = default_metrics()["sleep_hours"]
    h.engine.ingest(parse_health({"sleep": 400}, at(2026, 10, 21, 10), date(2026, 10, 21)))
    h.engine.ingest(parse_health({"sleep": 430}, at(2026, 10, 21, 10, 30), date(2026, 10, 21)))
    assert round(metric.measure(plan, goal, at(2026, 10, 21, 0), at(2026, 10, 21, 11), h.store), 2) == 7.17
