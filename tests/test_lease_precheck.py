"""The conversation side of lending a thread to a project's session: the
command route's pre-check, local-only rounds, and leased threads kept."""

from __future__ import annotations

import json

from conftest import at, make_harness
from muninn.core.llm_router import CloudFirst, LLMRouter
from muninn.core.understanding import Understanding


class FakeLeases:
    def __init__(self):
        self.calls = []
        self.leased: set[str] = set()

    def label(self, project):
        return project

    def attach(self, thread, project):
        self.calls.append(("attach", thread, project))
        self.leased.add(thread or "t-new")
        return {"thread": thread or "t-new", "lease_id": "L1", "state": "starting"}

    def detach(self, thread):
        self.calls.append(("detach", thread))
        self.leased.discard(thread)
        return {"thread": thread, "state": "detached"}

    def send(self, thread, text, client_msg_id, attachments=(), aside=""):
        self.calls.append(("send", thread, text, client_msg_id))
        return {"leased": True, "lease_id": "L1", "thread": thread}

    def lease_for(self, thread):
        return {"lease_id": "L1", "project": "garden"} if thread in self.leased else None



def no_model(h):
    def boom(*a, **k):
        raise AssertionError("a model was called")

    h.local.complete = h.cloud.complete = boom


def paired(web):
    h, client, devices = web
    client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "x"})
    return h, client


def test_slash_commands_and_a_leased_thread_never_reach_understanding(web):
    h, client = paired(web)
    h.engine.leases = leases = FakeLeases()
    no_model(h)
    r = client.post("/api/command", json={"text": "/attach garden"}).json()
    assert r == {"thread": "t-new", "lease_id": "L1", "state": "starting"}
    asked = []

    def judge(system, prompt, *, json_mode=False):  # only where the sentence goes and what in it is Muninn's
        asked.append(system)
        return '{"topic": "current", "for_muninn": null}'

    h.local.complete = judge
    r = client.post("/api/command", json={"text": "這株玫瑰要怎麼剪?", "thread": "t-new", "client_msg_id": "c1"}).json()
    assert r["leased"] and leases.calls[-1] == ("send", "t-new", "這株玫瑰要怎麼剪?", "c1")
    assert len(asked) == 1 and "lent to the project" in asked[0]  # judged once; never understood here
    no_model(h)
    assert client.post("/api/command", json={"text": "/detach", "thread": "t-new"}).json()["state"] == "detached"
    for text, thread in (("/detach", "t-new"), ("/attach", None), ("/whatever", None)):
        r = client.post("/api/command", json={"text": text, "thread": thread})
        assert r.status_code == 400 and r.json()["error"]


def test_slash_commands_without_leases_set_up_are_refused(web):
    h, client = paired(web)
    no_model(h)
    r = client.post("/api/command", json={"text": "/attach garden"})
    assert r.status_code == 400 and "還沒設定" in r.json()["error"]


def cloud_first(h):
    from muninn.core.evals import CaseRecorder

    router = LLMRouter([h.cloud, h.local], ["work"], CloudFirst(), CaseRecorder(h.store, h.clock))
    h.engine.router = router
    h.engine.understanding = Understanding(router)
    return router


def handover_round(h, model):
    """A round right after a deep talk came back: its handover line is in the conversation."""
    h.store.save_thread("t1", {"about": None, "ts": "x", "turns": [
        {"role": "user", "text": "/detach", "ts": "x"},
        {"role": "muninn", "text": "交接摘要:苗圃的報價在 repo 裡", "ts": "x", "local_only": True}]})
    replies = [json.dumps({"actions": [{"tool": "ask", "args": {"question": "報價多少?"}}], "reply": ""}), "大約三千"]

    def answer(system, prompt, *, json_mode=False):
        model.calls.append((system, prompt))
        return replies.pop(0)

    model.complete = answer
    h.engine.conversation().execute_turn(h.engine.conversation().understand_turn("t1", "那報價多少?"))
    return h.engine.conversation().thread("t1")


def test_a_round_with_a_handover_line_is_read_where_the_routing_says_and_never_cleared():
    h = make_harness(at(2026, 10, 22, 10))
    cloud_first(h)
    t = handover_round(h, h.cloud)
    assert len(h.cloud.calls) == 2 and h.local.calls == []  # routing and the answer: the cloud, as the user set it
    assert all(x.get("local_only") for x in t["turns"][-2:])  # the reply may repeat it: still only that session's projects
    assert not t["turns"][-2]["cloud_ok"]  # sensitive: never cleared, so never a cleared payload


def test_under_privacy_routing_a_round_with_a_handover_line_stays_on_the_local_model():
    h = make_harness(at(2026, 10, 22, 10))
    handover_round(h, h.local)
    assert h.cloud.calls == [] and len(h.local.calls) == 2


def test_every_conversation_is_kept_and_the_stream_opens_with_the_latest():
    h = make_harness(at(2026, 10, 22, 10))
    for i in range(35):
        h.engine.conversation().save_thread(f"t{i}", {"turns": [{"role": "user", "text": str(i), "ts": "x"}], "ts": f"2026-10-01T00:{i:02d}"})
    assert h.store.thread("t0")["turns"][0]["text"] == "0"  # none pruned
    feed = h.engine.conversation().feed()
    assert len(feed) == 30 and feed[0]["thread"] == "t5" and feed[-1]["thread"] == "t34"  # the latest thirty, oldest first
    older = h.engine.conversation().feed(before=feed[0]["ts"])
    assert [x["thread"] for x in older] == [f"t{i}" for i in range(5)]


def test_after_a_lent_session_only_its_project_hears_of_the_round():
    h = make_harness(at(2026, 10, 22, 10))
    from muninn.core.models import ExternalTodo

    class Records:
        def projects(self):
            return {"garden", "balcony"}

        def todos(self):
            return [ExternalTodo("garden", "me", "選玫瑰品種"), ExternalTodo("balcony", "me", "買配玫瑰的盆子")]

        def state(self, project):
            return None

    h.engine.records = Records()
    h.store.add_lease({"lease_id": "L1", "thread": "t1", "project": "garden", "mode": "l1", "state": "detached",
                       "started": "2026-10-22T09:00:00+08:00"})
    h.store.save_thread("t1", {"about": None, "ts": "x", "turns": [
        {"role": "muninn", "text": "交接摘要:選紅玫瑰", "ts": "x", "local_only": True, "via": "lease:L1"}]})
    replies = [json.dumps({"actions": [{"tool": "record_fact", "args": {"fact": "決定種紅玫瑰"}}], "reply": "好"}),
               json.dumps({"domain": "jobs", "summary": "決定種紅玫瑰", "projects": ["balcony"],
                           "effects": [{"id": "todo:garden:0", "effect": "resolves", "why": "選好了"},
                                       {"id": "todo:balcony:1", "effect": "changes", "why": "盆要配紅玫瑰"}]})]

    def local(system, prompt, *, json_mode=False):
        h.local.calls.append((system, prompt))
        return replies.pop(0)

    h.local.complete = local
    r = h.engine.conversation().execute_turn(h.engine.conversation().understand_turn("t1", "就種紅的"))
    assert [x["project"] for x in h.engine.handoffs()] == ["garden"]
    assert any("沒轉出:內容來自 garden 的深談" in o.text for o in r.outcomes)
    assert h.engine.conversation().thread("t1")["turns"][-1]["via"] == ["lease:L1"]  # the next rounds know where it came from


def test_a_task_closed_after_a_lent_session_tells_other_projects_only_that_it_is_done():
    h = make_harness(at(2026, 10, 22, 10))
    h.engine.messages.receive([{"id": "m1", "project": "garden", "type": "task", "text": "Choose the rose bed"},
                               {"id": "m2", "project": "blog", "type": "task", "text": "Choose the rose bed for the post"}])
    h.engine.tasks._match = lambda new, known: {"m2": "m1"}
    h.engine.tasks.sort_new()
    h.store.add_lease({"lease_id": "L1", "thread": "t1", "project": "garden", "mode": "l1", "state": "detached",
                       "started": "2026-10-22T09:00:00+08:00"})
    h.store.save_thread("t1", {"about": None, "ts": "x", "turns": [
        {"role": "muninn", "text": "交接摘要:南邊那塊", "ts": "x", "local_only": True, "via": "lease:L1"}]})
    h.local.reply = json.dumps({"actions": [{"tool": "close_task", "args": {"task": "m1", "why": "選南邊,土比較鬆"}}], "reply": "好"})
    h.engine.conversation().execute_turn(h.engine.conversation().understand_turn("t1", "那這件就定了"))
    told = {x["project"]: x["why"] for x in h.engine.handoffs()}
    assert told == {"garden": "選南邊,土比較鬆", "blog": ""}  # outside the lease's project: that it is done, not why


def test_the_conversations_kept_in_the_old_blob_move_into_rows_once(tmp_path):
    import sqlite3

    from muninn.adapters.store_sqlite import SqliteStore

    path = tmp_path / "m.db"
    SqliteStore(str(path))  # the tables
    db = sqlite3.connect(path)
    blob = {"a": {"about": {"kind": "idea", "id": 3}, "ts": "2026-10-20T10:00", "turns": [{"role": "user", "text": "澆花", "ts": "x"}]},
            "b": {"about": None, "ts": "2026-10-21T10:00", "turns": []}}
    db.execute("INSERT INTO state (key, value) VALUES ('threads', ?)", (json.dumps(blob, ensure_ascii=False),))
    db.commit()
    db.close()
    store = SqliteStore(str(path))
    assert store.thread("a")["turns"] == [{"role": "user", "text": "澆花", "ts": "x"}]
    assert [tid for tid, _ in store.threads_about("idea", "3")] == ["a"]
    assert [tid for tid, _ in store.threads(10)] == ["b", "a"]
    store.save_thread("c", {"about": None, "ts": "2026-10-22T10:00", "turns": []})
    assert len(SqliteStore(str(path)).threads(10)) == 3  # moved once: opening it again changes nothing
