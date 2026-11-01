"""An English Muninn says what waits on the user, the plan review, and what it did in English."""

from __future__ import annotations

import re
from datetime import date

from conftest import at, make_harness
from muninn.core import waiting
from muninn.languages import language

EN = language("en")


def no_chinese(text: str) -> bool:
    return re.search(r"[一-鿿]", text) is None


def test_what_waits_is_said_in_english():
    tasks = [{"key": k, "title": k, "due": due} for k, due in (("Book the vet", "2026-10-27"), ("Renew the permit", "2026-10-20"))]
    got = waiting.due_tasks(lambda: tasks, lambda: date(2026, 10, 26), words=EN)()
    assert [x.says for x in got] == ["Book the vet (due tomorrow)", "Renew the permit (overdue)"]
    left = {"lease": "L1", "project": "garden", "called": "Garden", "questions": ["Slope?", "Drain?"]}
    [w] = waiting.talk_questions(lambda: [("t9", {"talk_left": left})], words=EN)()
    assert w.says == "Garden's deep talk left 2 questions"
    [q] = waiting.project_questions(lambda: [{"id": "q1", "project": "garden", "text": "Red or white?"}], words=EN)()
    assert q.says == "garden asks you: Red or white?"
    [r] = waiting.plan_review(lambda: [{"id": "r1", "title": "Run", "detail": "3 h a week"}], words=EN)()
    assert r.says == "Add “Run: 3 h a week” to the plan?" and [s["label"] for s in r.suggest] == ["Yes", "No"]
    [p] = waiting.relinks(lambda: ["calendar"], words=EN)()
    assert p.says == "calendar needs reconnecting"


def test_the_plan_review_is_in_english():
    h = make_harness(at(2026, 10, 23, 20), confirm_all=False, lang="en")
    queue = h.engine.review_queue()
    details = {q["title"]: q["detail"] for q in queue}
    assert details["Go practice"] == "6 h a week" and details["Weekly applications"] == "4 applications a week"
    assert any(re.fullmatch(r"by \w{3} \d+ \(\d+ days? left\)", q["detail"]) for q in queue if q["kind"] == "deadline")
    rec = h.engine.review("jobs/goal:golang", "confirm")
    assert rec.text == "Confirmed: Go practice"
    says = [w.says for w in h.engine.waiting()]
    assert says and all(no_chinese(s) for s in says)
    [brief] = h.engine.morning()
    assert brief.data["push_title"].startswith("Good morning · ") and brief.body.startswith("Waiting on you: ")
    assert no_chinese(brief.data["push_title"])
