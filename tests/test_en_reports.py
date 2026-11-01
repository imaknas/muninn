"""An English Muninn's morning report, evening review, weekly report and drift alerts are in English."""

from __future__ import annotations

import re
from datetime import date

from conftest import at, make_harness
from muninn.core.drift import fmt_amount
from muninn.languages import language

EN = language("en")


def no_chinese(text: str) -> bool:
    return re.search(r"[一-鿿]", text) is None


def test_the_morning_report_is_in_english():
    h = make_harness(at(2026, 10, 22, 8, 30), lang="en")
    [item] = h.engine.morning()
    assert item.title == "Morning report, Thu Oct 22"
    assert item.body.startswith("Do first today:\n1. [Job search] Acme Staff Engineer: Apply; ask salary band (First batch, due Oct 24)")
    assert "— Job search —" in item.body and no_chinese(item.title + item.body)
    [[push]] = h.channel.sent
    assert push.text.startswith("Morning report · First: Acme Staff Engineer") and no_chinese(push.text)


def test_the_evening_review_and_the_weekly_report_are_in_english():
    h = make_harness(at(2026, 10, 22, 22, 30), lang="en")
    [evening] = h.engine.evening()
    assert evening.title == "Evening review, Oct 22" and "Do first tomorrow:" in evening.body
    assert no_chinese(evening.title + evening.body.replace("Cloud wording.", ""))
    [weekly] = h.engine.weekly()
    assert weekly.title == "Weekly report, week of Oct 19" and "• Deadline: " in weekly.body
    assert no_chinese(weekly.title + weekly.body)


def test_the_drift_alerts_and_the_goal_states_are_in_english():
    h = make_harness(at(2026, 10, 22, 10), lang="en")
    h.engine.evaluate()
    alerts = [i for i in h.store.inbox() if i.kind == "alert"]
    assert alerts and all(no_chinese(i.title + i.body) for i in alerts)
    assert any(re.search(r": \d+ days? left \(\w{3} \d+\)$|: due today$|overdue \(\w{3} \d+\)$", i.title) for i in alerts)
    goals = [g for d in h.engine.today_view()["domains"] for g in d["goals"]]
    assert goals and all(no_chinese(g["state_label"] + g["line"] + g["value"]) for g in goals)
    assert {g["state_label"].split(" ")[0] for g in goals} <= {"no", "kept", "over", "met", "behind", "on"}


def test_amounts_and_dates_are_written_the_english_way():
    assert fmt_amount(45, "min", EN) == "45 min" and fmt_amount(8200, "steps", EN) == "8,200 steps"
    assert EN("planner.due_on", due=date(2026, 11, 3)) == "due Nov 3" and EN("planner.overdue", days=1) == "1 day overdue"
    assert EN("reports.brief_title", day=date(2026, 11, 1)) == "Morning report, Sun Nov 1"
    assert EN("drift.todos_overdue", n=2) == "2 to-dos overdue" and EN("drift.todos_overdue", n=1) == "1 to-do overdue"
