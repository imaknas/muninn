from __future__ import annotations

from datetime import timedelta

from conftest import ZH, at, make_harness

from muninn.core.day_view import DayView
from muninn.core.metrics import estimate_hours, stretches
from muninn.core.models import CalendarEvent, Observation, Sensitivity


class Calendar:
    def __init__(self, events=(), own=(), broken=False):
        self._events, self._own, self.broken = list(events), list(own), broken

    def available(self):
        return True

    def events(self, start, end):
        if self.broken:
            raise RuntimeError("offline")
        return self._events

    def own_events(self, start, end):
        return self._own


def window(ts, minutes, domain, goal=None, title="editor"):
    return Observation("windows", ts, "window", f"w:{ts.isoformat()}:{domain}", {"duration_s": minutes * 60, "title": title},
                       domain, goal, Sensitivity.PERSONAL)


def view(h, calendar=None):
    e = h.engine
    return DayView(e.store, h.clock, e.tz, e.plans.load, e.metrics, e.stays_between, calendar, words=ZH)


def test_stretches_join_close_spans_and_keep_the_hours_estimate():
    t = at(2026, 10, 22, 9)
    spans = [(t, t + timedelta(minutes=10)), (t + timedelta(minutes=25), t + timedelta(minutes=30)), (t + timedelta(hours=2), t + timedelta(hours=2))]
    assert stretches(spans, timedelta(minutes=20)) == [(t, t + timedelta(minutes=30)), (t + timedelta(hours=2), t + timedelta(hours=2))]
    assert estimate_hours(spans, timedelta(minutes=20), timedelta(minutes=10)) == (30 + 10 + 0 + 10) / 60
    assert estimate_hours([], timedelta(minutes=20), timedelta(minutes=10)) == 0


def test_today_shows_activity_by_domain_without_window_titles():
    h = make_harness(at(2026, 10, 22, 12))
    h.store.add_observations([
        window(at(2026, 10, 22, 9), 30, "jobs", "jobs/golang", title="secret-repo — go"),
        window(at(2026, 10, 22, 9, 40), 20, "jobs", "jobs/golang"),
        window(at(2026, 10, 22, 10, 30), 15, "work"),
        window(at(2026, 10, 22, 11), 5, None),
        window(at(2026, 10, 21, 9), 60, "jobs"),  # yesterday
    ])
    d = view(h).today()
    assert [(a["title"], a["start"][11:16], a["end"][11:16]) for a in d["activity"]] == [
        ("Job search", "09:00", "10:10"), ("Work", "10:30", "10:55"), ("未歸類", "11:00", "11:15")]
    assert "secret-repo" not in str(d)
    assert d["hours"][0] == {"title": "Job search", "hours": 1.2}
    assert d["events"] == [] and d["stays"] == []


def test_today_lists_the_users_events_and_muninns_blocks_in_order():
    h = make_harness(at(2026, 10, 22, 12))
    cal = Calendar(events=[CalendarEvent("e1", at(2026, 10, 22, 13, 30), at(2026, 10, 22, 14), "Advisor meeting")],
                   own=[CalendarEvent("m1", at(2026, 10, 22, 10), at(2026, 10, 22, 12), "Muninn · Write chapter 3")])
    events = view(h, cal).today()["events"]
    assert [(e["title"], e["own"]) for e in events] == [("Write chapter 3", True), ("Advisor meeting", False)]
    assert view(h, Calendar(broken=True)).today()["events"] == []


def test_goal_days_give_each_day_of_the_week_and_nothing_for_days_to_come():
    h = make_harness(at(2026, 10, 22, 12))  # a Thursday
    h.store.add_observations([window(at(2026, 10, 20, 9), 60, "jobs", "jobs/golang"),
                              window(at(2026, 10, 22, 9), 30, "jobs", "jobs/golang")])
    days = view(h).goal_days()["jobs/golang"]
    assert [d["day"] for d in days][:1] == ["2026-10-19"] and len(days) == 7
    assert [d["value"] for d in days] == [0, round(70 / 60, 2), 0, round(40 / 60, 2), None, None, None]
