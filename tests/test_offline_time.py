from __future__ import annotations

from conftest import ZH, at, make_harness
from muninn.core.models import Observation, Sensitivity

NOT_COUNTED = ZH("offline_time.not_counted")


def win(hour, minute=0, minutes=30):
    return Observation("aw", at(2026, 10, 21, hour, minute), "window", f"w{hour}:{minute}",
                       {"app": "Code", "title": "x", "duration_s": minutes * 60}, sensitivity=Sensitivity.PERSONAL)


def asks(h):
    return [c for c in h.engine.pending_confirmations() if c.kind == "offline_time"]


def golang_hours(h):
    goals = {g["id"]: g for d in h.engine.today_view()["domains"] for g in d["goals"]}
    return goals["golang"]["actual"]


def test_a_reading_focus_becomes_one_question_and_counts_once_answered():
    h = make_harness(at(2026, 10, 21, 14))
    h.engine.focus("閱讀", True)
    h.clock.t = at(2026, 10, 21, 15, 30)
    h.engine.focus("閱讀", False)
    [q] = asks(h)
    assert "閱讀" in q.question and NOT_COUNTED in q.options
    before = golang_hours(h)
    title = next(t for t, qid in q.data["goals"].items() if qid == "jobs/golang")
    h.engine.answer(q.id, title)
    assert golang_hours(h) >= before + 1.4


def test_a_short_focus_asks_nothing():
    h = make_harness(at(2026, 10, 21, 14))
    h.engine.focus("閱讀", True)
    h.clock.t = at(2026, 10, 21, 14, 5)
    h.engine.focus("閱讀", False)
    assert asks(h) == []


def test_a_long_gap_between_activity_is_asked_about_once():
    h = make_harness(at(2026, 10, 21, 13))
    h.engine.ingest([win(7), win(10), win(12, 30)], evaluate=False)  # 07:00 is before waking hours
    assert h.engine.offline.ask_gaps() == 1
    [q] = asks(h)
    assert q.question.startswith("10:30–12:30")
    assert h.engine.offline.ask_gaps() == 0  # not twice
    h.engine.answer(q.id, NOT_COUNTED)
    assert not [o for o in h.store.observations(at(2026, 10, 21, 0), kinds=("offline",))]


def test_a_gap_the_calendar_explains_is_not_asked():
    h = make_harness(at(2026, 10, 21, 13))
    h.engine.offline._busy = lambda s, e: [(at(2026, 10, 21, 10, 30), at(2026, 10, 21, 12))]
    h.engine.ingest([win(10), win(12, 30)], evaluate=False)
    assert h.engine.offline.ask_gaps() == 0


def test_a_long_window_covering_short_ones_leaves_no_gap():
    h = make_harness(at(2026, 10, 21, 13))
    h.engine.ingest([win(9, minutes=180), win(10), win(12, 30)], evaluate=False)  # 09:00–12:00 in focus
    assert h.engine.offline.ask_gaps() == 0
