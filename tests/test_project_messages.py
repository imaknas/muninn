"""Projects talk to the user through Muninn: questions wait for an answer
that goes back as a handoff, progress and done land in the inbox, and 'done'
closes the handoff it names."""

from __future__ import annotations

import pytest

from muninn.core.project_messages import ProjectMessages
from conftest import ZH


@pytest.fixture
def pm(harness):
    return harness, ProjectMessages(harness.store, harness.clock, harness.engine.handoff_log, words=ZH)


def ask(mid, text="要選哪個方案?", project="home-budget"):
    return {"id": mid, "project": project, "type": "question", "text": text}


def test_a_question_waits_and_a_retried_post_is_a_no_op(pm):
    h, m = pm
    assert m.receive([ask("m1")]) == 1
    assert m.receive([ask("m1")]) == 0
    assert [(q["id"], q["project"], q["text"]) for q in m.open_questions()] == [("m1", "home-budget", "要選哪個方案?")]
    assert not h.store.inbox(limit=10)  # questions live on the home page, not in the inbox


def test_the_answer_goes_back_to_the_project_that_asked(pm):
    h, m = pm
    m.receive([ask("m1")])
    hand = m.answer("m1", " B 方案 ")
    assert hand["project"] == "home-budget" and hand["reply_to"] == "m1"
    assert hand["item"] == "要選哪個方案?" and hand["fact"] == "B 方案"
    assert [x["id"] for x in h.engine.handoffs()] == [hand["id"]]
    assert m.open_questions() == []
    assert m.answer("m1", "改成別家") is None  # answered once; a second answer needs a new question


def test_same_words_answering_two_questions_are_two_handoffs(pm):
    _, m = pm
    m.receive([ask("m1", "要開 PR 嗎?", "a"), ask("m2", "要開 PR 嗎?", "a")])
    assert m.answer("m1", "好")["id"] != m.answer("m2", "好")["id"]


def test_done_closes_its_handoff_and_tells_the_user(pm):
    h, m = pm
    hand = h.engine.handoff_log.add(project="resume", item="更新履歷", fact="f", effect="changes")
    m.receive([{"id": "d1", "project": "resume", "type": "done", "text": "履歷已更新", "handoff_id": hand["id"]}])
    assert [x["status"] for x in h.engine.handoffs(status=None)] == ["done"]
    item = h.store.inbox(limit=10)[0]
    assert item.title == "resume:處理完了" and item.body == "履歷已更新"


def test_progress_lands_in_the_inbox_without_touching_handoffs(pm):
    h, m = pm
    hand = h.engine.handoff_log.add(project="resume", item="更新履歷", fact="f", effect="changes")
    m.receive([{"id": "p1", "project": "resume", "type": "progress", "text": "改到一半", "handoff_id": hand["id"]}])
    assert [x["status"] for x in h.engine.handoffs(status=None)] == ["pending"]
    assert h.store.inbox(limit=10)[0].title == "resume:進度"


def test_a_question_let_go_tells_the_project_it_needs_no_answer(pm):
    h, m = pm
    m.receive([ask("m1")])
    hand = m.dismiss("m1")
    assert m.open_questions() == []
    assert (hand["project"], hand["reply_to"], hand["effect"]) == ("home-budget", "m1", "answers")
    assert "不用回答" in hand["fact"] and hand.get("quote") is None  # not the user's words: they said nothing
    assert m.dismiss("m1") is None and [x["id"] for x in h.engine.handoffs()] == [hand["id"]]


def test_a_deep_talks_own_questions_and_handover_posted_here_wait_nowhere(pm):
    h, m = pm
    m.receive([{**ask("q0", "提示要不要顯示範圍?", "garden"), "context": "lease:L1"},
               {"id": "pr", "project": "garden", "type": "progress", "text": "[garden 深談收回] 摘要", "context": "lease:L1"}])
    assert m.open_questions() == []  # the talk's handover keeps them with the talk
    assert h.store.inbox(limit=10) == []  # the summary is already in that conversation, kept local there


def test_a_question_settled_where_it_was_asked_leaves_the_list_without_an_answer_going_back(pm):
    h, m = pm
    m.receive([ask("m1"), ask("m2", "要不要跑 pilot?", "lab-notes")])
    m.receive([{"id": "m3", "project": "lab-notes", "type": "done", "question": "m2", "text": "他在 lab-notes 那邊決定先不跑"}])
    assert [q["id"] for q in m.open_questions()] == ["m1"]
    [settled] = h.store.project_messages(type="question", status="settled")
    assert settled["id"] == "m2" and "先不跑" in settled["answer"]
    assert h.engine.handoffs() == [] and not h.store.inbox(limit=10)  # nothing goes back, nothing to read
    m.receive([{"id": "m4", "project": "home-budget", "type": "drop", "question": "m1", "text": "不用問了"}])
    assert m.open_questions() == []


@pytest.mark.parametrize("bad", [
    {"id": "", "project": "a", "type": "question", "text": "t"},
    {"id": "x", "project": "../a", "type": "question", "text": "t"},
    {"id": "x", "project": "a", "type": "shout", "text": "t"},
    {"id": "x", "project": "a", "type": "done", "text": " "},
    {"id": "x", "project": "a", "type": "done", "text": "t", "handoff_id": "7"},
    {"id": "x", "project": "a", "type": "drop", "text": "t"},
    {"id": "x", "project": "a", "type": "done", "text": "t", "question": ""},
])
def test_one_bad_message_rejects_the_whole_post(pm, bad):
    h, m = pm
    with pytest.raises(ValueError):
        m.receive([ask("ok"), bad])
    assert m.open_questions() == [] and not h.store.inbox(limit=10)


def pair(client, devices):
    assert client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "iPhone"}).status_code == 200


def test_web_projects_post_with_the_token_and_the_user_answers_when_paired(web):
    h, client, devices = web
    msg = {"messages": [ask("m1")]}
    assert client.post("/api/messages", json=msg).status_code == 401
    assert client.post("/api/messages", json=msg, headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.post("/api/messages", json=msg, headers={"Authorization": "Bearer tok"})
    assert r.status_code == 200 and r.json() == {"ok": True, "new": 1}
    bad = client.post("/api/messages", json={"messages": [{"id": "x"}]}, headers={"Authorization": "Bearer tok"})
    assert bad.status_code == 400

    assert client.get("/api/project-questions").status_code == 401
    pair(client, devices)
    assert [q["id"] for q in client.get("/api/project-questions").json()["items"]] == ["m1"]
    r = client.post("/api/project-questions/m1/answer", json={"answer": "B 方案"})
    assert r.status_code == 200 and r.json()["ok"]
    assert h.engine.handoffs()[0]["reply_to"] == "m1"
    assert client.post("/api/project-questions/m1/answer", json={"answer": "again"}).status_code == 404


def test_a_database_from_before_context_gains_it_and_keeps_its_questions(tmp_path):
    import sqlite3
    from datetime import datetime, timezone

    from muninn.adapters.store_sqlite import SqliteStore

    db = sqlite3.connect(tmp_path / "old.db")
    db.execute("CREATE TABLE project_messages (id TEXT PRIMARY KEY, ts TEXT NOT NULL, project TEXT NOT NULL, type TEXT NOT NULL,"
               " text TEXT NOT NULL, handoff_id INTEGER, status TEXT NOT NULL, answer TEXT, changed TEXT NOT NULL)")
    db.execute("INSERT INTO project_messages VALUES ('old', 't', 'a', 'question', '舊問題', NULL, 'open', NULL, 't')")
    db.commit()
    db.close()
    store = SqliteStore(tmp_path / "old.db")
    store.add_project_message({"id": "new", "project": "a", "type": "question", "text": "新問題", "context": "lease:L"},
                              datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert [(m["id"], m["context"]) for m in store.project_messages(type="question")] == [("new", "lease:L"), ("old", None)]


class TwoProjects:
    def projects(self):
        return {"garden", "balcony"}

    def todos(self):
        return []

    def state(self, project):
        return None


def asked(h, project="garden", question="苗圃的報價問到了嗎?"):
    import json

    h.engine.records = TwoProjects()
    h.local.reply = json.dumps({"actions": [{"tool": "ask_project", "args": {"project": project, "question": question}}],
                                "reply": ""}, ensure_ascii=False)
    conv = h.engine.conversation()
    turn = conv.understand_turn(None, "去問一下 garden 那邊報價怎樣了")
    return conv.execute_turn(turn), conv, turn["thread"]


def test_a_question_asked_of_a_project_comes_back_where_it_was_asked(harness):
    h = harness
    r, conv, tid = asked(h)
    assert "問了 garden" in r.outcomes[0].text
    [hd] = h.engine.handoffs()
    assert (hd["project"], hd["effect"], hd["fact"], hd["thread"]) == ("garden", "asks", "苗圃的報價問到了嗎?", tid)
    h.engine.messages.receive([{"id": "r1", "project": "garden", "type": "progress", "text": "報價 3200,週五前回覆", "handoff_id": hd["id"]}])
    last = conv.thread(tid)["turns"][-1]
    assert last["text"] == "garden 回:報價 3200,週五前回覆" and last["local_only"] and last["via"] == ["project:garden"]
    assert h.engine.handoff_log.get(hd["id"])["status"] == "done"
    h.engine.messages.receive([{"id": "r2", "project": "garden", "type": "done", "text": "已處理", "handoff_id": hd["id"]}])
    assert conv.thread(tid)["turns"][-1] == last  # closing it after the answer says nothing more
    h.engine.messages.receive([{"id": "r3", "project": "garden", "type": "progress", "text": "補充:另一家報價 2900", "handoff_id": hd["id"]}])
    assert conv.thread(tid)["turns"][-1]["text"] == "garden 回:補充:另一家報價 2900"  # what it adds is said too
    assert not [i for i in h.store.inbox(limit=10) if i.kind == "info"]  # said in the conversation, not listed
    assert [m.title for batch in h.channel.sent for m in batch][-1] == "garden 回了"
    assert h.engine.lease_projects(["project:garden"]) == {"garden"}  # what it said goes on only to garden



def test_acknowledging_the_records_leaves_a_question_that_goes_the_old_way_to_be_sent(harness):
    h = harness
    asked(h)
    records, cursor = h.engine.records_out.since("garden", 0)
    assert records == [] and h.engine.records_ack("garden", cursor) == cursor
    [hd] = h.engine.handoffs()  # still pending: the platform takes it from the handoffs, not the records
    assert hd["effect"] == "asks"

def test_a_question_for_no_such_project_is_not_sent(harness):
    h = harness
    r, _, _ = asked(h, project="gardn")
    assert r.outcomes[0].failed and "沒有叫 gardn 的專案" in r.outcomes[0].text and h.engine.handoffs() == []


def test_a_project_closing_a_question_it_never_answered_is_said_as_that(harness):
    h = harness
    r, conv, tid = asked(h)
    [hd] = h.engine.handoffs()
    h.engine.messages.receive([{"id": "d1", "project": "garden", "type": "done", "text": "(這個專案把問題結案了,但沒有回答)", "handoff_id": hd["id"]}])
    assert conv.thread(tid)["turns"][-1]["text"] == "garden 把這個問題結案了,沒有回答。"
