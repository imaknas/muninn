"""In English, what Muninn says about the projects' messages, and what it tells a project about its questions, is in
English; a question let go of is still told as dismissed (core/project_messages.py, core/handoff_records.py,
core/onboarding.py). Invented projects only."""

from __future__ import annotations

import re
from datetime import timedelta

from muninn.core import ph
from muninn.languages import language

from conftest import at, make_harness

P = "garden"
EN = language("en")


def english(text: str) -> bool:
    return re.search(r"[一-鿿]", text) is None


def rec(type_, key, ext="acts/0.4", id_=None, origin="requester", by="hub:registrar", **more):
    return {"ph": "0.4", "ext": ext, "id": id_ or f"{type_[0]}:{key}", "type": type_, "origin": origin, "by": by,
            "key": key, "project": P, "ts": "2026-10-22T09:00:00Z", **more}


def out(h):
    records, cursor = h.engine.records_out.since(P, 0)
    h.engine.records_ack(P, cursor)
    exts = ph.extensions(__import__("pathlib").Path(__file__).resolve().parents[1] / "contracts" / "vendor" / "principal-handoff")
    for r in records:
        assert ph.validate(r, exts[r["ext"]], h.engine.parties)[1] == [], r
    return records


def test_progress_lands_in_the_inbox_titled_in_english():
    h = make_harness(at(2026, 10, 22, 10), lang="en")
    h.engine.messages.receive([{"id": "p1", "project": P, "type": "progress", "text": "Seeds are in"}])
    assert h.store.inbox(limit=10)[0].title == "garden: progress"


def test_a_question_let_go_tells_the_project_in_english_and_is_still_a_dismissal():
    h = make_harness(at(2026, 10, 22, 10), lang="en")
    q = {"ext": "decisions/0.4"}
    h.engine.records_in.receive([{**rec("raised", "dec-a", id_="r:dec-a", terms={"text": "Red or white roses?"}, terms_rev=1), **q}])
    hand = h.engine.messages.dismiss("dec-a")
    assert hand["fact"].startswith("This question needs no answer") and hand["dismissed"] is True
    [s] = [r for r in out(h) if r["ext"] == "decisions/0.4"]
    assert s["outcome"] == "dismissed" and s["note"] == "You skipped this question in Muninn" and "answer" not in s


def test_a_projects_claim_names_whose_it_was_in_english():
    h = make_harness(at(2026, 10, 22, 10), lang="en")
    h.engine.records_in.receive([rec("raised", "task-pots-1", id_="r:task-pots-1", terms={"text": "Buy clay pots"}, terms_rev=1)])
    h.engine.records_in.receive([rec("claimed", "task-pots-1", id_="c:task-pots-1:agent", origin="agent", by="hub:garden",
                                     outcome="done", evidence={"basis": "the receipt is in the repo"})])
    [c] = [c for c in h.engine.pending_confirmations() if c.kind == "action"]
    assert english(c.question) and c.data["action"]["why"] == "the receipt is in the repo"  # asked, with the project's reason
    assert EN("handoff_records.withdrawn", reason="settled") == "Decided on the project's side"
    assert EN("handoff_records.withdrawn", reason=None) == "No longer needed"


def test_a_new_users_first_conversation_is_named_in_english():
    h = make_harness(at(2026, 10, 22, 10), plans=[], lang="en")
    assert h.engine.about("onboarding", "first")["title"] == "Set up your first domain"
