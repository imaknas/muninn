"""The built-in schedule: which job is due when, kept across restarts, retried a few times a day."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from muninn.adapters import scheduler
from muninn.adapters.scheduler import Schedule, plan

TPE = timezone(timedelta(hours=8))


class Clock:
    def __init__(self, t):
        self.t = t

    def now(self):
        return self.t


def schedule(jobs, clock, codes=None, kept=None):
    kept = {} if kept is None else kept
    ran = []

    def run(job, args):
        ran.append(job)
        return (codes or {}).get(job, 0)

    s = Schedule(plan(jobs), "Asia/Taipei", clock.now, run, kept.get, kept.__setitem__)
    return s, ran, kept


def settle(monkeypatch):
    """Jobs run in the calling thread, so each round's results are there when it returns."""
    monkeypatch.setattr(scheduler.threading, "Thread", lambda target, args=(), **kw: type(
        "T", (), {"start": lambda self: target(*args)})())


def test_unset_runs_nothing_and_default_is_every_job():
    assert plan(None) == {} and set(plan("default")) == set(scheduler.COMMANDS)
    assert set(plan({"weekly": None, "tick": "5m"})) == set(scheduler.COMMANDS) - {"weekly"}
    with pytest.raises(ValueError, match="e.g. 15m"):
        plan({"tick": "often"})
    with pytest.raises(ValueError, match="unknown job"):
        plan({"lunch": "12:00"})


def test_a_job_at_a_time_runs_once_that_day_even_across_a_restart(monkeypatch):
    settle(monkeypatch)
    clock = Clock(datetime(2026, 11, 3, 8, 0, tzinfo=TPE))
    s, ran, kept = schedule({"morning": "08:30", "tick": None, "evening": None, "reflect": None, "reflection": None,
                             "weekly": None}, clock)
    assert s.round() == []  # not yet
    clock.t = datetime(2026, 11, 3, 8, 31, tzinfo=TPE)
    assert s.round() == ["morning"] and s.round() == []
    again, ran2, _ = schedule({"morning": "08:30", **{j: None for j in scheduler.COMMANDS if j != "morning"}}, clock, kept=kept)
    assert again.round() == []  # restarted: already done today
    clock.t = datetime(2026, 11, 4, 8, 30, tzinfo=TPE)
    assert again.round() == ["morning"]


def test_a_failing_job_is_tried_again_three_times_a_day_at_most(monkeypatch):
    settle(monkeypatch)
    clock = Clock(datetime(2026, 11, 3, 22, 31, tzinfo=TPE))
    s, ran, _ = schedule({"evening": "22:30", **{j: None for j in scheduler.COMMANDS if j != "evening"}}, clock, {"evening": 1})
    for _ in range(5):
        s.round()
    assert ran == ["evening"] * 3


def test_an_interval_job_and_one_on_certain_days(monkeypatch):
    settle(monkeypatch)
    clock = Clock(datetime(2026, 11, 3, 10, 0, tzinfo=TPE))  # a Tuesday
    off = {j: None for j in scheduler.COMMANDS}
    s, ran, _ = schedule({**off, "tick": "15m", "weekly": "sun 21:00"}, clock)
    assert s.round() == ["tick"] and s.round() == []
    clock.t += timedelta(minutes=15)
    assert s.round() == ["tick"]
    clock.t = datetime(2026, 11, 8, 21, 5, tzinfo=TPE)  # Sunday
    assert set(s.round()) == {"tick", "weekly"}
