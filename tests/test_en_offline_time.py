"""An English Muninn asks about time away from the keyboard, unplaced windows and unplaced repos in English."""

from __future__ import annotations

import json
import re

from conftest import at, make_harness
from muninn.core.models import Observation, Sensitivity
from muninn.core.window_classify import WindowClassifier


def no_chinese(text: str) -> bool:
    return re.search(r"[一-鿿]", text) is None


def window(hour, minute=0, minutes=30, title="x"):
    return Observation("aw", at(2026, 10, 21, hour, minute), "window", f"w{hour}:{minute}:{title}",
                       {"app": "Code", "title": title, "duration_s": minutes * 60}, sensitivity=Sensitivity.PERSONAL)


def asks(h, kind):
    return [c for c in h.engine.pending_confirmations() if c.kind == kind]


def test_a_focus_is_asked_about_and_its_answer_logged_in_english():
    h = make_harness(at(2026, 10, 21, 14), lang="en")
    h.engine.focus("Reading", True)
    h.clock.t = at(2026, 10, 21, 15, 30)
    h.engine.focus("Reading", False)
    [q] = asks(h, "offline_time")
    assert q.question == "“Reading” was on for 1.5 h just now. Which goal does it count toward?"
    assert q.options[-1] == "Doesn't count (a break, personal)"
    title = next(t for t, qid in q.data["goals"].items() if qid == "jobs/golang")
    h.engine.answer(q.id, title)
    [rec] = [a for a in h.store.actions(at(2026, 10, 21, 0)) if a.undo.get("kind") == "offline"]
    assert rec.text == f"Counted toward “{title}”: “Reading” was on for 1.5 h just now"


def test_a_gap_is_asked_about_in_english():
    h = make_harness(at(2026, 10, 21, 13), lang="en")
    h.engine.ingest([window(10), window(12, 30)], evaluate=False)
    assert h.engine.offline.ask_gaps() == 1
    [q] = asks(h, "offline_time")
    assert q.question == "The computer sat idle 10:30–12:30 (2.0 h). What were you doing? Which goal does it count toward?"


def test_an_unplaced_window_and_repo_are_asked_about_in_english():
    h = make_harness(at(2026, 10, 21, 20), lang="en")
    h.engine.window_classifier = WindowClassifier(h.engine.router)
    h.engine.ingest([window(0, minutes=90, title="Notes")], evaluate=False)
    h.local.reply = json.dumps({"windows": [{"title": "Notes", "domain": "jobs", "sure": False}]})
    h.engine.classify_windows()
    h.local.reply = json.dumps({"windows": [{"title": "Notes", "domain": "learning", "sure": False}]})
    h.engine.recheck_windows()
    [q] = asks(h, "window_class")
    assert q.question.startswith("“Notes” (Code) was open 1.5 h this week") and q.options[-1] == "None of these"
    assert no_chinese(q.question)
    h.engine.ingest([Observation("git", at(2026, 10, 21, 9), "commit", "g1", {"repo": "side-thing"}, sensitivity=Sensitivity.PERSONAL)],
                    evaluate=False)
    h.local.reply = '{"domain": "jobs"}'
    assert h.engine.suggest_attributions() == 1
    [a] = asks(h, "attribution")
    assert a.question.startswith("Which domain does the activity in the repo “side-thing” belong to?") and no_chinese(a.question)
