from __future__ import annotations

from conftest import at, make_harness


def post(h, *messages):
    return h.engine.messages.receive(list(messages))


def task(mid, project, text, **kw):
    return {"id": mid, "project": project, "type": "task", "text": text, **kw}


def matcher(h, pairs):
    h.engine.tasks._match = lambda new, known: {k: v for k, v in pairs.items() if k in {t["key"] for t in new}}


def test_a_task_from_a_project_is_kept_once_by_its_key():
    h = make_harness(at(2026, 10, 21, 21))
    post(h, task("m1", "garden", "Ask the nursery which roses survive frost", due="2026-11-01"))
    post(h, task("m1", "garden", "Ask the nursery which roses survive frost"))  # retried
    [t] = h.engine.tasks.open()
    assert t["key"] == "m1" and t["due"] == "2026-11-01" and [o["project"] for o in t["origins"]] == ["garden"]
    assert h.engine.tasks.get("T-1")["key"] == "m1"


def test_closing_tells_every_origin_and_undo_reopens():
    h = make_harness(at(2026, 10, 21, 21))
    post(h, task("m1", "garden", "Ask the nursery about roses"), task("m2", "balcony", "Ask nursery: frost-proof roses?"))
    matcher(h, {"m2": "m1"})
    assert h.engine.tasks.sort_new() == 1
    [t] = h.engine.tasks.open()
    assert {o["project"] for o in t["origins"]} == {"garden", "balcony"}
    rec = h.engine.tasks.close("m1", why="nursery said: the red ones")
    told = {(x["project"], x["reply_to"], x["effect"]) for x in h.engine.handoffs()}
    assert told == {("garden", "m1", "done"), ("balcony", "m2", "done")}
    h.engine.undo_action(rec.id)
    assert h.engine.tasks.get("m1")["status"] == "open"
    assert h.engine.handoffs() == []  # the close had not left yet: withdrawn, nothing to reopen
    rec = h.engine.tasks.close("m1")
    for x in h.engine.handoffs():
        h.engine.set_handoff_status(x["id"], "delivered")
    h.engine.undo_action(rec.id)
    assert {(x["project"], x["effect"]) for x in h.engine.handoffs()} == {("garden", "reopened"), ("balcony", "reopened")}


def test_done_in_a_project_session_closes_it_without_telling_that_project():
    h = make_harness(at(2026, 10, 21, 21))
    h.engine.tasks.grant_claims_mandate("專案說做完了就記成做完,我可以撤回")
    post(h, task("m1", "garden", "Order soil"))
    post(h, {"id": "d1", "project": "garden", "type": "done", "text": "ordered", "task": "m1"})
    assert h.engine.tasks.get("m1")["status"] == "done" and h.engine.handoffs() == []


def test_a_done_that_arrives_first_leaves_the_task_born_closed():
    h = make_harness(at(2026, 10, 21, 21))
    post(h, {"id": "d1", "project": "garden", "type": "done", "text": "ordered", "task": "m9"})
    post(h, task("m9", "garden", "Order soil"))
    t = h.engine.tasks.get("m9")
    assert t["status"] == "done" and t["title"] == "Order soil" and h.engine.tasks.open() == []


def test_a_finished_task_sent_again_goes_back_as_already_done_unless_new():
    h = make_harness(at(2026, 10, 21, 21))
    post(h, task("m1", "garden", "Order soil"))
    h.engine.tasks.close("m1")
    post(h, task("m2", "garden", "Order soil (from yesterday's notes)"), task("m3", "garden", "Order soil", new_reason="second bed"))
    matcher(h, {"m2": "m1", "m3": "m1"})
    h.engine.tasks.sort_new()
    assert [t["key"] for t in h.engine.tasks.open()] == ["m3"]  # the one that said why it is new
    [back] = [x for x in h.engine.handoffs() if x["effect"] == "already_done"]
    assert back["reply_to"] == "m2" and back["task"] == "m1"


def test_a_merge_can_be_undone():
    h = make_harness(at(2026, 10, 21, 21))
    post(h, task("m1", "garden", "Ask the nursery about roses"), task("m2", "balcony", "Ask about roses"))
    matcher(h, {"m2": "m1"})
    h.engine.tasks.sort_new()
    merge = next(a for a in h.store.actions(at(2026, 10, 21, 0)) if a.undo.get("kind") == "task_merge")
    h.engine.undo_action(merge.id)
    assert sorted(t["key"] for t in h.engine.tasks.open()) == ["m1", "m2"]


def test_a_task_linked_to_a_deadline_closes_with_it():
    h = make_harness(at(2026, 10, 21, 21))
    t = h.engine.tasks.add("Call the bank", links=["deadline:jobs/call-bank"])
    h.engine.mark_deadline_done("jobs/call-bank")
    assert h.engine.tasks.get(t["key"])["status"] == "done"


def test_a_task_the_evidence_shows_done_is_closed_with_undo():
    import json

    h = make_harness(at(2026, 10, 21, 21))
    post(h, task("m1", "garden", "Ask the nursery which roses survive frost"), task("m2", "garden", "Order soil"))
    h.store.save_digest("s1", "garden", at(2026, 10, 21, 20), {"summary": "nursery replied: the red roses survive frost",
                                                                "decisions": [], "commitments": []}, at(2026, 10, 21, 20))
    h.local.reply = json.dumps({"done": [{"task": "m1", "why": "苗圃回覆了"}, {"task": "made-up", "why": "?"}]})

    assert h.engine.completion_detector().run() == 1
    assert [t["key"] for t in h.engine.tasks.open()] == ["m2"]
    undo = next(a for a in h.store.actions(at(2026, 10, 21, 0)) if a.undo.get("kind") == "task_status")
    h.engine.undo_action(undo.id)
    assert sorted(t["key"] for t in h.engine.tasks.open()) == ["m1", "m2"]
    calls = len(h.local.calls)
    assert h.engine.completion_detector().run() == 0 and len(h.local.calls) == calls  # nothing new since: not asked


def test_no_trustworthy_answer_leaves_the_new_tasks_for_next_time():
    h = make_harness(at(2026, 10, 21, 21))
    post(h, task("m1", "garden", "Order soil"), task("m2", "balcony", "Buy soil"))
    h.engine.tasks._match = lambda new, known: None
    assert h.engine.tasks.sort_new() == 0
    matcher(h, {"m2": "m1"})
    assert h.engine.tasks.sort_new() == 1  # tried again


def test_a_migration_is_compared_in_batches():
    h = make_harness(at(2026, 10, 21, 21))
    post(h, *[task(f"m{i}", "garden", f"Chore number {i}") for i in range(25)])
    calls = []
    h.engine.tasks._match = lambda new, known: calls.append(len(new)) or {}
    h.engine.tasks.sort_new()
    assert calls == [20, 5]


def test_settled_by_any_project_goes_back_as_already_done():
    h = make_harness(at(2026, 10, 21, 21))
    post(h, task("m1", "garden", "Pick tiles"))
    h.engine.tasks.close("m1", "dropped")
    post(h, task("m2", "balcony", "Pick tiles for the floor"))
    matcher(h, {"m2": "m1"})
    h.engine.tasks.sort_new()
    assert h.engine.tasks.open() == []
    [back] = [x for x in h.engine.handoffs() if x["effect"] == "already_done"]
    assert back["project"] == "balcony" and "已決定不做" in back["fact"]


def test_one_project_dropping_a_shared_task_leaves_it_to_the_others():
    h = make_harness(at(2026, 10, 21, 21))
    post(h, task("m1", "garden", "Ask about roses"), task("m2", "balcony", "Ask about roses too"))
    matcher(h, {"m2": "m1"})
    h.engine.tasks.sort_new()
    post(h, {"id": "x", "project": "balcony", "type": "drop", "text": "not needed", "task": "m2"})
    [t] = h.engine.tasks.open()
    assert [o["project"] for o in t["origins"]] == ["garden"]


def test_a_merge_keeps_the_due_date_and_links():
    h = make_harness(at(2026, 10, 21, 21))
    mine = h.engine.tasks.add("Call the bank", links=["deadline:jobs/call-bank"])
    post(h, task("m1", "garden", "Call the bank about the loan", due="2026-11-02"))
    matcher(h, {"m1": mine["key"]})
    h.engine.tasks.sort_new()
    [t] = h.engine.tasks.open()
    assert t["key"] == mine["key"] and t["due"] == "2026-11-02" and "deadline:jobs/call-bank" in t["links"]


def test_a_close_inferred_from_evidence_waits_before_the_projects_hear():
    h = make_harness(at(2026, 10, 21, 21))
    post(h, task("m1", "garden", "Order soil"))
    h.engine.tasks.close("m1", by="evidence", why="the shop's receipt mail")
    assert h.engine.handoffs() == []
    h.clock.t = at(2026, 10, 22, 10)
    assert [x["effect"] for x in h.engine.handoffs()] == ["done"]


def test_no_closed_record_for_a_short_number():
    h = make_harness(at(2026, 10, 21, 21))
    post(h, {"id": "x", "project": "garden", "type": "done", "text": "?", "task": "T-99"})
    assert h.store.tasks(status=None) == []


def test_evidence_from_before_the_tasks_arrived_still_counts():
    import json


    h = make_harness(at(2026, 10, 21, 20))
    h.store.save_digest("s1", "garden", at(2026, 10, 21, 19), {"summary": "soil ordered", "decisions": [], "commitments": []},
                        at(2026, 10, 21, 19))
    assert h.engine.completion_detector().run() == 0  # no tasks yet
    h.clock.t = at(2026, 10, 21, 21)
    post(h, task("m1", "garden", "Order soil"))
    h.local.reply = json.dumps({"done": [{"task": "m1", "why": "對話裡說訂了"}]})
    assert h.engine.completion_detector().run() == 1


def test_a_close_the_sender_only_inferred_is_asked_first():
    from conftest import at, make_harness

    h = make_harness(at(2026, 10, 22, 10))
    e = h.engine
    e.messages.receive([{"id": "task-garden-1", "project": "garden", "type": "task", "text": "Deploy the greenhouse sensor"},
                        {"id": "task-garden-2", "project": "garden", "type": "task", "text": "Order seeds"}])
    e.messages.receive([{"id": "done-task-garden-1", "project": "notes", "type": "done", "task": "task-garden-1", "inferred": True,
                         "text": "推斷已完成:今天的紀錄提到感測器已上線"}])
    assert e.tasks.get("task-garden-1")["status"] == "open"  # not closed on a guess
    [ask] = [c for c in e.pending_confirmations() if c.kind == e.gate.ASK]
    assert ask.question == "T-1「Deploy the greenhouse sensor」做完了嗎?" and ask.data["why"] == "推斷已完成:今天的紀錄提到感測器已上線"
    e.answer(ask.id, "做完了,結案")  # the user says it is done
    assert e.tasks.get("task-garden-1")["status"] == "done"
    # a project that saw it done (no `inferred`) is asked about too: its own word settles nothing by itself
    e.messages.receive([{"id": "done-task-garden-2", "project": "garden", "type": "done", "task": "task-garden-2", "text": "下單了"}])
    assert e.tasks.get("task-garden-2")["status"] == "open" and [c for c in e.pending_confirmations() if c.kind == e.gate.ASK]


def test_under_his_standing_permission_a_projects_word_closes_it_and_a_guess_is_still_asked():
    from conftest import at, make_harness

    h = make_harness(at(2026, 10, 22, 10))
    e = h.engine
    e.tasks.grant_claims_mandate("專案說做完了就記成做完,我可以撤回")
    e.messages.receive([{"id": "task-garden-1", "project": "garden", "type": "task", "text": "Deploy the greenhouse sensor"},
                        {"id": "task-garden-2", "project": "garden", "type": "task", "text": "Order seeds"}])
    e.messages.receive([{"id": "done-task-garden-2", "project": "garden", "type": "done", "task": "task-garden-2", "text": "下單了"}])
    assert e.tasks.get("task-garden-2")["status"] == "done"  # taken at once, undoably
    e.messages.receive([{"id": "done-task-garden-1", "project": "notes", "type": "done", "task": "task-garden-1", "inferred": True,
                         "text": "推斷已完成"}])
    assert e.tasks.get("task-garden-1")["status"] == "open"  # a guess is not the project's word: asked
    assert e.tasks.revoke_claims_mandate() and e.tasks.claims_mandate() is None
    # a guess about a task Muninn never had leaves nothing behind
    e.messages.receive([{"id": "done-x", "project": "notes", "type": "done", "task": "task-garden-9", "inferred": True, "text": "?"}])
    assert e.tasks.get("task-garden-9") is None


def test_inferred_must_be_a_boolean():
    import pytest

    from muninn.core.project_messages import parse

    with pytest.raises(ValueError):
        parse({"id": "x", "project": "p", "type": "done", "task": "k", "text": "t", "inferred": "yes"})
    assert parse({"id": "x", "project": "p", "type": "done", "task": "k", "text": "t"})["inferred"] is False


def test_an_unreadable_evidence_answer_leaves_the_evidence_for_next_time():
    import json

    h = make_harness(at(2026, 10, 21, 21))
    post(h, task("m1", "garden", "Ask the nursery which roses survive frost"))
    h.store.save_digest("s1", "garden", at(2026, 10, 21, 20), {"summary": "nursery replied: the red roses survive frost",
                                                                "decisions": [], "commitments": []}, at(2026, 10, 21, 20))
    h.local.reply = "Sorry, I am not sure."  # nothing to read
    assert h.engine.completion_detector().run() == 0
    h.local.reply = json.dumps({"done": [{"task": "m1", "why": "苗圃回覆了"}]})
    assert h.engine.completion_detector().run() == 1  # the same evidence, looked at again


def test_a_project_dropping_the_only_ask_leaves_the_task_open_and_asks_him():
    h = make_harness(at(2026, 10, 21, 21))
    post(h, task("m1", "garden", "Order soil"))
    post(h, {"id": "x", "project": "garden", "type": "drop", "text": "not needed", "task": "m1"})
    assert h.engine.tasks.get("m1")["status"] == "open"  # it may be something he promised someone
    [c] = [c for c in h.engine.pending_confirmations() if c.kind == "action"]
    h.engine.answer(c.id, c.options[0])
    assert h.engine.tasks.get("m1")["status"] == "dropped"
