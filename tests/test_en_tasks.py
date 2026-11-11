"""In English, what the task registry asks the user, logs, and tells the projects that asked is in English
(core/tasks.py, and the task actions in core/actions.py). Invented projects and tasks only."""

from __future__ import annotations

import re

from conftest import at, make_harness


def english(text: str) -> bool:
    return re.search(r"[一-鿿]", text) is None


def post(h, *messages):
    return h.engine.messages.receive(list(messages))


def task(mid, project, text, **kw):
    return {"id": mid, "project": project, "type": "task", "text": text, **kw}


def test_a_close_only_guessed_is_asked_in_english_and_one_no_project_needs_is_asked_whether_still_to_do():
    h = make_harness(at(2026, 10, 21, 21), lang="en")
    post(h, task("m1", "garden", "Order potting soil"), task("m2", "balcony", "Measure the railing"))
    h.engine.tasks.close("m1", by="garden", inferred=True)
    [c] = [c for c in h.engine.pending_confirmations() if c.kind == "action"]
    assert (c.question, tuple(c.options)) == ('garden says T-1 "Order potting soil" is done. Is it?', ("Done, close it", "Not yet"))
    h.engine.answer(c.id, "Done, close it")
    assert h.engine.tasks.get("m1")["status"] == "done"
    assert h.engine.tasks.withdrawn("m2", "balcony") == "asked"
    [d] = [c for c in h.engine.pending_confirmations() if c.kind == "action"]
    assert d.question == 'balcony says it no longer needs T-2 "Measure the railing". Do you still need to do it?' and tuple(d.options) == ("No, close it", "Still to do")
    assert d.data.get("why") == "balcony no longer needs it"


def test_a_merge_a_new_date_and_an_undone_close_are_said_in_english():
    h = make_harness(at(2026, 10, 21, 21), lang="en")
    post(h, task("m1", "garden", "Ask the nursery about roses"), task("m2", "balcony", "Ask the nursery: frost-proof roses?"))
    h.engine.tasks._match = lambda new, known: {"m2": "m1"} if any(t["key"] == "m2" for t in new) else {}
    assert h.engine.tasks.sort_new() == 1
    [merged] = [a for a in h.engine.undoable_actions() if "merged" in a.text]
    assert merged.text == '"Ask the nursery: frost-proof roses?" is the same as T-1; merged'
    h.engine.tasks.set_due("m1", "2026-11-02")
    notes = [x["fact"] for x in h.store.handoffs(status=None) if x.get("effect") == "premise"]
    assert notes and set(notes) == {"The user moved its deadline to 2026-11-02"}
    rec = h.engine.tasks.close("m1")
    assert rec.text == "Done: T-1 Ask the nursery about roses"
    for x in h.engine.handoffs():
        h.engine.set_handoff_status(x["id"], "delivered")
    h.engine.undo_action(rec.id)
    reopened = [x for x in h.engine.handoffs() if x["effect"] == "reopened"]
    assert reopened and {x["why"] for x in reopened} == {"The earlier completion was undone"}
    assert all(english(x["why"]) for x in reopened)


def test_a_task_sent_again_after_it_was_settled_is_told_so_in_english():
    h = make_harness(at(2026, 10, 21, 21), lang="en")
    post(h, task("m1", "garden", "Order potting soil"))
    h.engine.tasks.close("m1")
    post(h, task("m9", "garden", "Order potting soil"))
    h.engine.tasks._match = lambda new, known: {"m9": "m1"} if any(t["key"] == "m9" for t in new) else {}
    h.engine.tasks.sort_new()
    told = [x for x in h.store.handoffs(status=None) if x.get("effect") == "already_done"]
    assert told and told[0]["fact"] == "This was done on 2026-10-21 (T-1)"
    assert told[0]["why"].startswith("This resends something already settled")
