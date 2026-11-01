"""Muninn as the principal side of Principal Handoff 0.4: the projects' records carried out through the tasks and
questions, and what Muninn tells them read back as records that say who decided, valid by the specification."""

from __future__ import annotations

from datetime import timedelta

import pytest

from muninn.core import ph
from muninn.package import CONTRACTS

from conftest import at, make_harness

P = "garden"


def rec(type_, key, ext="acts/0.4", id_=None, origin="requester", by="hub:registrar", **more):
    return {"ph": "0.4", "ext": ext, "id": id_ or f"{type_[0]}:{key}", "type": type_, "origin": origin, "by": by,
            "key": key, "project": P, "ts": "2026-10-22T09:00:00Z", **more}


def raised(key, text, **terms):
    return rec("raised", key, id_=f"r:{key}", terms={"text": text, **terms}, terms_rev=1)


@pytest.fixture
def h():
    return make_harness(at(2026, 10, 22, 10))


def out(h, project=P):
    """What the project reads, and has (it acknowledges what it read)."""
    records, cursor = h.engine.records_out.since(project, 0)
    h.engine.records_ack(project, cursor)
    exts = ph.extensions(CONTRACTS / "vendor" / "principal-handoff")
    for r in records:  # everything Muninn tells is a valid record from the principal side (in 0.4, whatever its label)
        assert ph.validate(ph.upgraded(r), exts[ph.upgraded(r)["ext"]], h.engine.parties)[1] == [], r
    return records


def test_a_raised_task_is_kept_and_its_amendment_changes_its_words(h):
    got = h.engine.records_in.receive([raised("task-pots-1", "買陶盆", due="2026-10-30")])
    assert got == [{"id": "r:task-pots-1", "status": "accepted"}]
    assert h.engine.records_in.receive([raised("task-pots-1", "買陶盆", due="2026-10-30")])[0]["status"] == "duplicate"
    h.engine.records_in.receive([rec("amended", "task-pots-1", id_="a:task-pots-1:2", terms={"text": "買兩個陶盆"}, terms_rev=2,
                                     supersedes=["r:task-pots-1"])])
    t = h.engine.tasks.get("task-pots-1")
    assert (t["title"], t["due"]) == ("買兩個陶盆", "2026-10-30")


def test_a_bad_record_is_refused_alone_and_the_users_settlements_are_only_muninns(h):
    got = h.engine.records_in.receive([
        rec("settled", "task-x", origin="principal", outcome="done", terms_rev=1,
            authority={"kind": "principal", "decider": h.engine.parties.principal}),
        raised("task-pots-2", "量陽台"),
        {"ext": "tickets/1.0", "id": "x"}])
    assert [g["status"] for g in got] == ["invalid", "accepted", "unsupported"]


def test_a_projects_claim_alone_is_asked_and_his_yes_adopts_it_bound_to_the_terms(h):
    from muninn.core import ph

    need = raised("task-pots-1", "買陶盆")
    h.engine.records_in.receive([need])
    h.engine.records_in.receive([rec("claimed", "task-pots-1", id_="c:task-pots-1:agent", origin="agent", by="hub:garden",
                                     outcome="done", evidence={"basis": "收據在 repo 裡"})])
    assert h.engine.tasks.get("task-pots-1")["status"] == "open"  # the project's own word is no evidence apart from it
    [c] = [c for c in h.engine.pending_confirmations() if c.kind == "action"]
    h.engine.answer(c.id, c.options[0])
    h.clock.t += timedelta(hours=1)  # past any hold for an undo
    [s] = [r for r in out(h) if r["type"] == "settled"]
    assert s["authority"] == {"kind": "confirmed", "decider": h.engine.parties.principal} and s["adopts"] == ["c:task-pots-1:agent"]
    assert s["terms_rev"] == 1 and s["terms_digest"] == ph.terms_digest(ph.upgraded(need)) and s["ts"].endswith("Z")


def test_an_inferred_claim_is_asked_and_his_yes_is_recorded_as_confirmed(h):
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆")])
    h.engine.records_in.receive([rec("claimed", "task-pots-1", id_="c:task-pots-1:r0", origin="registrar", outcome="done",
                                     evidence={"basis": "那一行從 STATE.md 消失了"})])
    assert h.engine.tasks.get("task-pots-1")["status"] == "open"
    [c] = [c for c in h.engine.pending_confirmations() if c.kind == "action"]
    h.engine.answer(c.id, c.options[0])
    h.clock.t += timedelta(hours=1)
    [s] = [r for r in out(h) if r["type"] == "settled"]
    assert s["authority"] == {"kind": "confirmed", "decider": h.engine.parties.principal}
    assert s["evidence"]["proposal"] == f"muninn:question:{c.id}"


def test_his_own_close_is_the_principals_and_a_reopen_supersedes_it(h):
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆")])
    r = h.engine.tasks.close("task-pots-1", why="買好了")
    h.clock.t += timedelta(hours=1)
    [s] = [x for x in out(h) if x["type"] == "settled"]
    assert s["authority"] == {"kind": "principal", "decider": h.engine.parties.principal} and s["outcome"] == "done"
    assert h.engine.undo_action(r.id)
    reopened = [x for x in out(h) if x["type"] == "reopened"]
    assert reopened and reopened[0]["supersedes"] == [s["id"]] and reopened[0]["restore_text"] == "買陶盆"


def test_a_close_held_for_an_undo_is_not_told_until_it_comes_due_and_one_taken_back_never(h):
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆"), raised("task-pots-2", "量陽台")])
    first = h.engine.tasks.close("task-pots-1", by="evidence", why="他說買好了")  # Muninn's reading: held for an undo
    [held] = [x for x in h.engine.handoff_log.pending(include_held=True) if x.get("not_before")]
    records, cursor = h.engine.records_out.since(P, 0)
    assert records == [] and cursor < held["id"]
    h.engine.tasks.close("task-pots-2", by="evidence", why="量好了")
    assert h.engine.undo_action(first.id)  # taken back while held: never told
    h.clock.t += timedelta(hours=13)
    assert [(x["type"], x["key"]) for x in out(h)] == [("settled", "task-pots-2")]


def test_a_project_no_longer_needing_the_only_ask_leaves_it_open_and_asks_him(h):
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆")])
    h.engine.records_in.receive([rec("withdrawn", "task-pots-1", id_="w1", reason="not_needed", supersedes=["r:task-pots-1"])])
    assert h.engine.tasks.get("task-pots-1")["status"] == "open"  # it may be something he promised: he is asked
    [c] = [c for c in h.engine.pending_confirmations() if c.kind == "action"]
    assert "還要做嗎" in c.question


def test_a_question_raised_waits_for_him_and_his_answer_is_the_principals(h):
    q = {"ext": "decisions/0.4"}
    h.engine.records_in.receive([{**rec("raised", "dec-rose", id_="r:dec-rose", terms={"text": "紅的還白的?"}, terms_rev=1), **q}])
    [open_q] = h.engine.messages.open_questions()
    assert (open_q["id"], open_q["text"]) == ("dec-rose", "紅的還白的?")
    h.engine.records_in.receive([{**rec("amended", "dec-rose", id_="a:dec-rose:2", terms={"text": "紅的、白的還粉的?"}, terms_rev=2,
                                        supersedes=["r:dec-rose"]), **q}])
    assert h.engine.messages.open_questions()[0]["text"] == "紅的、白的還粉的?"
    h.engine.messages.answer("dec-rose", "紅的")
    [s] = [r for r in out(h) if r["ext"] == "decisions/0.4"]
    assert (s["outcome"], s["answer"], s["terms_rev"]) == ("decided", {"text": "紅的"}, 2)
    assert s["authority"] == {"kind": "principal", "decider": h.engine.parties.principal}


def test_a_question_let_go_is_dismissed_and_one_answered_in_the_project_leaves_his_list(h):
    q = {"ext": "decisions/0.4"}
    h.engine.records_in.receive([{**rec("raised", "dec-a", id_="r:dec-a", terms={"text": "A?"}, terms_rev=1), **q},
                                 {**rec("raised", "dec-b", id_="r:dec-b", terms={"text": "B?"}, terms_rev=1), **q}])
    h.engine.messages.dismiss("dec-a")
    [s] = [r for r in out(h) if r["ext"] == "decisions/0.4"]
    assert s["outcome"] == "dismissed" and "answer" not in s
    h.engine.records_in.receive([{**rec("claimed", "dec-b", id_="c:dec-b", origin="agent", by="hub:garden", outcome="decided",
                                        answer={"text": "B 選二"}, evidence={"basis": "他在 session 裡說的"}), **q}])
    assert h.engine.messages.open_questions() == []


def test_a_merge_and_a_date_he_moved_are_told_as_a_relation_and_a_note(h):
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆"), raised("task-pots-2", "買陶土盆")])
    h.engine.tasks._match = lambda new, known: {"task-pots-2": "task-pots-1"}
    h.engine.tasks.sort_new()
    h.engine.tasks.set_due("task-pots-1", "2026-11-02")
    records = out(h)
    [rel] = [r for r in records if r["type"] == "related"]
    assert rel["key"] == "task-pots-2" and rel["relation"]["target"] == {"ext": "acts/0.4", "project": P, "key": "task-pots-1"}
    assert any(r["type"] == "note" and r["about"] == {"key": "task-pots-1"} and "2026-11-02" in r["note"] for r in records)
    assert not [x for x in h.engine.handoffs() if x.get("effect") in ("same_as", "premise")]  # never a v1 handoff


def test_a_handoff_that_makes_no_valid_record_is_never_told(h):
    h.engine.handoff_log.add(project=P, item="壞的", fact="x", effect="done", reply_to="has:colon", decided={"by": "user"})
    assert h.engine.records_out.since(P, 0)[0] == []


def test_the_records_are_read_on_from_a_cursor_and_only_the_projects_own(h):
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆")])
    h.engine.tasks.set_due("task-pots-1", "2026-11-02")
    first, cursor = h.engine.records_out.since(P, 0)
    assert first and h.engine.records_out.since(P, cursor) == ([], cursor)
    assert h.engine.records_out.since("blog", 0)[0] == []
    assert h.engine.records_ack(P, cursor) == cursor


def test_the_web_takes_and_gives_records_with_the_token(tmp_path):
    from starlette.testclient import TestClient

    from muninn.adapters.channels.webpush import VapidKeys
    from muninn.adapters.web.app import create_app
    from muninn.adapters.web.devices import DeviceRegistry

    h = make_harness(at(2026, 10, 22, 10))
    keys = VapidKeys(tmp_path / "secrets" / "vapid.pem")
    keys.ensure()
    client = TestClient(create_app(h.engine, keys, "tok", DeviceRegistry(tmp_path / "dev.db")), base_url="https://testserver")
    auth = {"Authorization": "Bearer tok"}
    assert client.post("/api/records", json={"records": []}).status_code == 401
    r = client.post("/api/records", headers=auth, json={"records": [raised("task-pots-1", "買陶盆")]}).json()
    assert r["results"] == [{"id": "r:task-pots-1", "status": "accepted"}]
    h.engine.tasks.set_due("task-pots-1", "2026-11-02")
    got = client.get(f"/api/records?project={P}&after=0", headers=auth).json()
    assert got["records"][0]["type"] == "note" and got["cursor"] > 0
    assert client.post("/api/records/ack", headers=auth, json={"project": P, "through": got["cursor"]}).json()["through"] == got["cursor"]
    assert client.get(f"/api/records?project={P}").status_code == 401



def test_a_project_still_speaking_0_3_is_read_as_0_4_and_answered_as_it_speaks(h):
    old = {**raised("task-pots-1", "買陶盆"), "ph": "0.3", "ext": "acts/0.3"}
    assert h.engine.records_in.receive([old]) == [{"id": "r:task-pots-1", "status": "accepted"}]
    assert h.engine.records_in.receive([old])[0]["status"] == "duplicate"  # sent again: the same record
    h.engine.tasks.close("task-pots-1", why="買好了")
    h.clock.t += timedelta(hours=1)
    [s] = [r for r in out(h) if r["type"] == "settled"]
    assert (s["ph"], s["ext"]) == ("0.3", "acts/0.3")
    assert s["terms_digest"] == ph.terms_digest(ph.upgraded(old))  # over the record as 0.4 reads it (acts section 8)
    h.engine.records_in.receive([raised("task-pots-2", "量陽台")])  # it speaks 0.4 now: answered so from here on
    h.engine.tasks.close("task-pots-2", why="量好了")
    h.clock.t += timedelta(hours=1)
    assert [(r["ph"], r["ext"]) for r in out(h) if r["type"] == "settled" and r["key"] == "task-pots-2"] == [("0.4", "acts/0.4")]


def test_a_task_that_came_the_v1_way_is_settled_the_v1_way(h):
    h.engine.messages.receive([{"id": "m1", "project": P, "type": "task", "text": "買陶盆"}])
    [t] = h.engine.tasks.open()
    h.engine.tasks.close(t["key"], why="買好了")
    h.clock.t += timedelta(hours=1)
    assert out(h) == []  # no terms to bind a settlement to
    assert [x["effect"] for x in h.engine.handoff_log.pending()] == ["done"]


def test_muninns_times_only_rise_and_another_projects_item_is_named_only_by_a_uri(h):
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆")])
    h.engine.messages.receive([{"id": "m9", "project": "kitchen", "type": "task", "text": "買陶盆"}])
    h.engine.records_in.receive([{**raised("task-pots-2", "量陽台"), "id": "r:task-pots-2"}])
    for key in ("task-pots-1", "task-pots-2"):  # told in the same instant
        h.engine.tasks.close(key, why="好了")
    h.clock.t += timedelta(hours=1)
    told = out(h)
    times = [ph.instant(r["ts"]) for r in told]
    assert times == sorted(times) and len(set(times)) == len(times)
    rel = h.engine.handoff_log  # a same_as to another project's item: no project name or key leaves
    h.engine.handoff_log.same_as(P, "task-pots-1", {"project": "kitchen", "key": "m9"})
    h.clock.t += timedelta(hours=1)
    [r] = [r for r in out(h) if r["type"] == "related"]
    assert set(r["relation"]["target"]) == {"uri"} and "kitchen" not in r["relation"]["target"]["uri"] and rel


def test_an_act_raised_again_under_another_key_waits_beside_it_and_is_his_to_settle(h):
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆"), raised("task-pots-2", "買兩個陶盆")])
    h.engine.records_in.receive([rec("related", "task-pots-1", id_="rel:1", relation={"kind": "replaced_by", "active": True,
                                                                                        "target": {"ext": "acts/0.4", "project": P, "key": "task-pots-2"}}),
                                 rec("withdrawn", "task-pots-1", id_="w:1", reason="replaced", supersedes=["r:task-pots-1"])])
    assert h.engine.tasks.get("task-pots-1")["status"] == "open" and not h.engine.pending_confirmations()  # not asked apart
    h.engine.tasks.close("task-pots-2", why="買好了")
    [c] = [c for c in h.engine.pending_confirmations() if c.kind == "replaced"]
    assert "買陶盆" in c.question and "買兩個陶盆" in c.question and c.data["neutral"]  # beside it, nothing put forward
    assert h.engine.tasks.get("task-pots-1")["status"] == "open"  # never settled from the replacement's settlement
    h.engine.answer(c.id, c.options[1])
    h.clock.t += timedelta(hours=1)
    [s] = [r for r in out(h) if r["type"] == "settled" and r["key"] == "task-pots-1"]
    assert (s["outcome"], s["authority"]["kind"]) == ("dropped", "principal")


def test_a_record_with_a_member_name_twice_is_refused_alone(tmp_path):
    import json

    from starlette.testclient import TestClient

    from muninn.adapters.channels.webpush import VapidKeys
    from muninn.adapters.web.app import create_app
    from muninn.adapters.web.devices import DeviceRegistry

    h = make_harness(at(2026, 10, 22, 10))
    keys = VapidKeys(tmp_path / "secrets" / "vapid.pem")
    keys.ensure()
    client = TestClient(create_app(h.engine, keys, "tok", DeviceRegistry(tmp_path / "dev.db")), base_url="https://testserver")
    twice = json.dumps(raised("task-pots-1", "買陶盆"), ensure_ascii=False)[:-1] + ', "key": "task-pots-9"}'
    body = '{"records": [' + twice + ", " + json.dumps(raised("task-pots-2", "量陽台"), ensure_ascii=False) + "]}"
    got = client.post("/api/records", content=body.encode(), headers={"Authorization": "Bearer tok", "Content-Type": "application/json"}).json()
    assert [r["status"] for r in got["results"]] == ["invalid", "accepted"]


def test_records_kept_under_0_3_read_as_0_4_and_ids_are_unique_per_origin(tmp_path):
    import json
    import sqlite3

    from muninn.adapters.store_sqlite import SqliteStore

    db = tmp_path / "m.db"
    SqliteStore(db)
    con = sqlite3.connect(db)  # as an older Muninn kept them
    con.execute("DROP TABLE ph_records")
    con.execute("CREATE TABLE ph_records (seq INTEGER PRIMARY KEY AUTOINCREMENT, ext TEXT NOT NULL, project TEXT NOT NULL, id TEXT NOT NULL,"
                " key TEXT NOT NULL, record TEXT NOT NULL, received TEXT NOT NULL, UNIQUE (ext, project, id))")
    old = {**raised("task-pots-1", "買陶盆"), "ph": "0.3", "ext": "acts/0.3"}
    con.execute("INSERT INTO ph_records (ext, project, id, key, record, received) VALUES (?,?,?,?,?,?)",
                ("acts/0.3", P, old["id"], old["key"], json.dumps(old), "2026-10-22T09:00:00+00:00"))
    con.commit()
    con.close()
    store = SqliteStore(db)
    [kept] = store.records_of("acts/0.4", P, "task-pots-1")
    assert (kept["ph"], kept["ext"]) == ("0.4", "acts/0.4")
    assert store.add_record(ph.upgraded(old), at(2026, 10, 22, 10), ph.canonical) == "duplicate"
    assert store.get_state(ph.SPEAKS) == {P: "0.3"}  # answered as it last spoke, until it sends again
    agents = {**ph.upgraded(old), "origin": "agent", "type": "note", "about": {"key": "task-pots-1"}}
    assert store.add_record(agents, at(2026, 10, 22, 10), ph.canonical) == "new"  # the same id from another origin



def test_under_his_standing_permission_an_agents_claim_is_settled_as_delegated_and_a_registrars_is_still_asked(h):
    h.engine.tasks.grant_claims_mandate("專案說做完了就記成做完,我可以撤回")
    [m] = [h.engine.tasks.claims_mandate()]
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆"), raised("task-pots-2", "量陽台")])
    h.engine.records_in.receive([rec("claimed", "task-pots-1", id_="c:1:agent", origin="agent", by="hub:garden", outcome="done",
                                     evidence={"basis": "收據在 repo 裡"}),
                                 rec("claimed", "task-pots-2", id_="c:2:r0", origin="registrar", outcome="done",
                                     evidence={"basis": "被打勾"})])
    assert h.engine.tasks.get("task-pots-1")["status"] == "done" and h.engine.tasks.get("task-pots-2")["status"] == "open"
    h.clock.t += timedelta(hours=1)
    [s] = [r for r in out(h) if r["type"] == "settled"]
    assert s["authority"] == {"kind": "delegated", "decider": "muninn", "under": f"muninn:mandate:{m['id']}",
                              "chain": [h.engine.parties.principal, "muninn"]} and "adopts" not in s
