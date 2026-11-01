"""An English Muninn's own lines (core/assembly.py): what it logs, pushes and says when a project answers or a login lapses."""

from __future__ import annotations

import re

from conftest import RecordingLLM, at, make_harness

HAN = re.compile(r"[一-鿿]")


def pushed(h) -> list[str]:
    return [m.text for batch in h.channel.sent for m in batch]


def test_a_deadline_reported_done_is_logged_in_english():
    h = make_harness(at(2026, 10, 21, 10), lang="en")
    done = h.engine.mark_deadline_done("jobs/call-bank")
    assert done.text == "Done: Call the bank"


def test_a_project_closing_a_question_unanswered_is_said_and_pushed_in_english():
    h = make_harness(at(2026, 10, 21, 10), lang="en")
    h.engine.project_answered({"project": "greenhouse"}, {"type": "done"})
    assert "greenhouse answered" in pushed(h)
    h.engine.project_answered({"project": "greenhouse"}, {"type": "answer", "text": "Tuesday works"})
    said = [turn["text"] for _, t in h.store.threads(10) for turn in t["turns"]]
    assert "greenhouse closed this question without answering." in said and "greenhouse answered: Tuesday works" in said


def test_a_lapsed_cloud_login_is_pushed_in_english():
    h = make_harness(at(2026, 10, 21, 10), lang="en")
    cloud = RecordingLLM("cloud-a:big", False, label="Cloud A", login_hint="Sign in again on Cloud A's settings page")
    h.engine.cloud_login_lost(cloud, "Please run /login")
    texts = pushed(h)
    assert any(t.startswith("The Cloud A login has lapsed") for t in texts)
    assert all(HAN.search(t) is None for t in texts)


def test_a_muninn_that_cannot_tell_its_version_says_so_in_english():
    h = make_harness(at(2026, 10, 21, 10), lang="en")
    assert h.engine.requests.version.unknown == "Nothing is set up to tell which version runs"
