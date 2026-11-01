"""Muninn as the principal side of Principal Handoff 0.5: the projects' records carried out through the tasks and
questions, and what Muninn tells them read back as records that say who decided, valid by the specification in the
version each project speaks."""

from __future__ import annotations

from datetime import timedelta

import pytest

from muninn.core import ph
from muninn.package import CONTRACTS

from conftest import at, make_harness

P = "garden"


def rec(type_, key, ext="acts/0.5", id_=None, origin="requester", by="hub:registrar", **more):
    return {"ph": ext.split("/")[1], "ext": ext, "id": id_ or f"{type_[0]}:{key}", "type": type_, "origin": origin, "by": by,
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
    exts = ph.extensions(CONTRACTS / "vendor" / "principal-handoff", CONTRACTS / "vendor" / "principal-handoff-0.4")
    for r in records:  # everything Muninn tells is a valid record from the principal side, in the version it is told in
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
    q = {"ext": "decisions/0.5"}
    h.engine.records_in.receive([{**rec("raised", "dec-rose", id_="r:dec-rose", terms={"text": "紅的還白的?"}, terms_rev=1), **q}])
    [open_q] = h.engine.messages.open_questions()
    assert (open_q["id"], open_q["text"]) == ("dec-rose", "紅的還白的?")
    h.engine.records_in.receive([{**rec("amended", "dec-rose", id_="a:dec-rose:2", terms={"text": "紅的、白的還粉的?"}, terms_rev=2,
                                        supersedes=["r:dec-rose"]), **q}])
    assert h.engine.messages.open_questions()[0]["text"] == "紅的、白的還粉的?"
    h.engine.messages.answer("dec-rose", "紅的")
    [s] = [r for r in out(h) if r["ext"] == "decisions/0.5"]
    assert (s["outcome"], s["answer"], s["terms_rev"]) == ("decided", {"text": "紅的"}, 2)
    assert s["authority"] == {"kind": "principal", "decider": h.engine.parties.principal}


def test_a_question_let_go_is_dismissed_and_one_answered_in_the_project_leaves_his_list(h):
    q = {"ext": "decisions/0.5"}
    h.engine.records_in.receive([{**rec("raised", "dec-a", id_="r:dec-a", terms={"text": "A?"}, terms_rev=1), **q},
                                 {**rec("raised", "dec-b", id_="r:dec-b", terms={"text": "B?"}, terms_rev=1), **q}])
    h.engine.messages.dismiss("dec-a")
    [s] = [r for r in out(h) if r["ext"] == "decisions/0.5"]
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
    assert rel["key"] == "task-pots-2" and rel["relation"]["target"] == {"ext": "acts/0.5", "project": P, "key": "task-pots-1"}
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



def test_a_project_is_told_in_the_version_it_is_configured_to_speak_and_first_spoke(h, caplog):
    old = {**raised("task-pots-1", "買陶盆"), "ph": "0.3", "ext": "acts/0.3"}
    assert h.engine.records_in.receive([old]) == [{"id": "r:task-pots-1", "status": "accepted"}]
    assert h.engine.records_in.receive([old])[0]["status"] == "duplicate"  # sent again: the same record
    assert h.engine.records_in.receive([raised("task-pots-1", "買陶盆")])[0]["status"] == "duplicate"  # and in 0.5 too
    h.engine.tasks.close("task-pots-1", why="買好了")
    h.clock.t += timedelta(hours=1)
    [s] = [r for r in out(h) if r["type"] == "settled"]
    assert (s["ph"], s["ext"]) == ("0.3", "acts/0.3")
    h.engine.records_in.receive([raised("task-pots-2", "量陽台")])  # a 0.5 record is read, but what it is told is configured
    assert "configured to speak 0.3" in caplog.text
    h.engine.handoff_versions.set("*", "acts", "0.5")  # every project, this one too, and those to come
    assert h.engine.handoff_versions.of("blog", "acts") == "0.5"
    h.engine.tasks.close("task-pots-2", why="量好了")
    h.clock.t += timedelta(hours=1)
    [s] = [r for r in out(h) if r["type"] == "settled" and r["key"] == "task-pots-2"]
    assert (s["ph"], s["ext"], s["terms_digest"]) == ("0.5", "acts/0.5", ph.terms_digest(raised("task-pots-2", "量陽台")))


def test_a_project_that_speaks_0_4_is_checked_under_0_4_and_told_in_it_bound_to_04s_digest(h):
    need = rec("raised", "task-pots-1", ext="acts/0.4", id_="r:task-pots-1", terms={"text": "買陶盆"}, terms_rev=1)
    claim = rec("claimed", "task-pots-1", ext="acts/0.4", id_="c:1", origin="agent", by="hub:garden", outcome="done",
                evidence={"verifiable": [{"id": "x", "format": "f", "statement": "s"}]})  # no terms_rev: invalid in 0.4 only
    assert [g["status"] for g in h.engine.records_in.receive([need, claim])] == ["accepted", "invalid"]
    h.engine.tasks.close("task-pots-1", why="買好了")
    h.clock.t += timedelta(hours=1)
    [s] = [r for r in out(h) if r["type"] == "settled"]
    assert (s["ph"], s["ext"]) == ("0.4", "acts/0.4")
    assert s["terms_digest"] == ph._terms_digest_04(need) != ph.terms_digest(need)


def test_a_task_that_came_the_v1_way_and_is_raised_later_is_the_same_task_in_the_words_raised(h):
    h.engine.messages.receive([{"id": "task-pots-1", "project": P, "type": "task", "text": "買陶盆"}])
    h.engine.records_in.receive([{**raised("task-pots-1", "買兩個陶盆", due="2026-11-05"), "context": "migration-v1"}])
    [t] = h.engine.tasks.open()
    assert (t["key"], t["title"], t["due"]) == ("task-pots-1", "買兩個陶盆", "2026-11-05")
    h.engine.tasks.close("task-pots-1", why="買好了")
    h.clock.t += timedelta(hours=1)
    [s] = [r for r in out(h) if r["type"] == "settled"]  # now a record, bound to the terms raised
    assert s["terms_digest"] == ph.terms_digest(raised("task-pots-1", "買兩個陶盆", due="2026-11-05"))


def test_an_item_merged_into_another_task_changes_only_its_own_words_there(h):
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆"), raised("task-pots-2", "買陶土盆")])
    h.engine.tasks._match = lambda new, known: {"task-pots-2": "task-pots-1"}
    h.engine.tasks.sort_new()
    h.engine.records_in.receive([rec("amended", "task-pots-2", id_="a:task-pots-2:2", terms={"text": "買三個陶土盆"}, terms_rev=2,
                                     supersedes=["r:task-pots-2"])])
    t = h.engine.tasks.get("task-pots-1")
    assert t["title"] == "買陶盆" and {o["ref"]: o["text"] for o in t["origins"]}["task-pots-2"] == "買三個陶土盆"


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


def replace(h, old, new, rel_id="rel:1"):
    h.engine.records_in.receive([rec("related", old, id_=rel_id, relation={"kind": "replaced_by", "active": True,
                                                                         "target": {"ext": "acts/0.5", "project": P, "key": new}}),
                                 rec("withdrawn", old, id_=f"w:{old}", reason="replaced", supersedes=[f"r:{old}"])])


def replaced_question(h):
    [c] = [c for c in h.engine.pending_confirmations() if c.kind == "replaced"]
    return c


def test_an_act_raised_again_is_asked_about_with_its_replacement_and_is_his_to_settle(h):
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆"), raised("task-pots-2", "買兩個陶盆")])
    replace(h, "task-pots-1", "task-pots-2")
    c = replaced_question(h)
    assert "買陶盆" in c.question and "買兩個陶盆" in c.question and c.data["together"] and c.data["neutral"]
    assert h.engine.tasks.get("task-pots-1")["status"] == "open"
    h.engine.answer(c.id, c.options[0])  # the same as the new one
    h.clock.t += timedelta(hours=1)
    [s] = [r for r in out(h) if r["type"] == "settled" and r["key"] == "task-pots-1"]
    assert (s["outcome"], s["authority"]["kind"]) == ("dropped", "principal") and "買兩個陶盆" in s["note"]
    assert h.engine.tasks.get("task-pots-2")["status"] == "open"


def test_once_its_replacement_is_settled_a_replaced_act_is_asked_about_on_its_own_never_settled_with_it(h):
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆"), raised("task-pots-2", "買兩個陶盆")])
    replace(h, "task-pots-1", "task-pots-2")
    together = replaced_question(h)
    h.engine.tasks.close("task-pots-2", why="買好了")
    c = replaced_question(h)
    assert c.id != together.id and not c.data["together"] and c.data["neutral"]  # the earlier question taken back
    assert h.engine.tasks.get("task-pots-1")["status"] == "open"  # never settled from the replacement's settlement
    h.engine.answer(c.id, c.options[1])
    h.clock.t += timedelta(hours=1)
    [s] = [r for r in out(h) if r["type"] == "settled" and r["key"] == "task-pots-1"]
    assert (s["outcome"], s["authority"]["kind"]) == ("dropped", "principal")


def test_a_replaced_act_whose_replacement_is_taken_back_or_withdrawn_is_asked_about_on_its_own(h):
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆"), raised("task-pots-2", "買兩個陶盆"), raised("task-pots-3", "量陽台"),
                                 raised("task-pots-4", "量兩個陽台")])
    replace(h, "task-pots-1", "task-pots-2")
    h.engine.records_in.receive([rec("related", "task-pots-1", id_="rel:2", supersedes=["rel:1"],
                                     relation={"kind": "replaced_by", "active": False,
                                               "target": {"ext": "acts/0.5", "project": P, "key": "task-pots-2"}})])
    assert not replaced_question(h).data["together"]  # no longer its single replacement
    replace(h, "task-pots-3", "task-pots-4", rel_id="rel:3")
    h.engine.records_in.receive([rec("withdrawn", "task-pots-4", id_="w:4", reason="not_needed", supersedes=["r:task-pots-4"])])
    [c] = [c for c in h.engine.pending_confirmations() if c.kind == "replaced" and c.data["task"] == "task-pots-3"]
    assert not c.data["together"] and "量兩個陽台" in c.question  # what became of its replacement is said


def test_a_replaced_act_he_keeps_is_not_asked_about_again_and_a_claim_on_it_is_still_his_to_settle(h):
    h.engine.tasks.grant_claims_mandate("專案說做完了就記成做完,我可以撤回")
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆"), raised("task-pots-2", "買兩個陶盆")])
    replace(h, "task-pots-1", "task-pots-2")
    h.engine.answer(replaced_question(h).id, replaced_question(h).options[2])  # both still to do
    h.engine.tasks.close("task-pots-2", why="買好了")
    assert not [c for c in h.engine.pending_confirmations() if c.kind == "replaced"]
    h.engine.records_in.receive([rec("claimed", "task-pots-1", id_="c:1:agent", origin="agent", by="hub:garden", outcome="done",
                                     evidence={"basis": "收據在 repo 裡"})])
    assert h.engine.tasks.get("task-pots-1")["status"] == "open"  # not under the standing permission: he is asked


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


def test_records_are_kept_as_they_came_and_an_id_is_unique_per_origin_and_type(tmp_path):
    import json
    import sqlite3

    from muninn.adapters.store_sqlite import SqliteStore

    db = tmp_path / "m.db"
    SqliteStore(db)
    con = sqlite3.connect(db)  # as Muninn kept them under 0.4
    con.execute("DROP TABLE ph_records")
    con.execute("CREATE TABLE ph_records (seq INTEGER PRIMARY KEY AUTOINCREMENT, ext TEXT NOT NULL, project TEXT NOT NULL,"
                " origin TEXT NOT NULL, id TEXT NOT NULL, key TEXT NOT NULL, record TEXT NOT NULL, received TEXT NOT NULL,"
                " UNIQUE (ext, project, origin, id))")
    old = rec("raised", "task-pots-1", ext="acts/0.4", id_="r:task-pots-1", terms={"text": "買陶盆"}, terms_rev=1)
    con.execute("INSERT INTO ph_records (ext, project, origin, id, key, record, received) VALUES (?,?,?,?,?,?,?)",
                ("acts/0.4", P, "requester", old["id"], old["key"], json.dumps(old), "2026-10-22T09:00:00+00:00"))
    con.commit()
    con.close()
    store = SqliteStore(db)
    assert store.records_of("acts", P, "task-pots-1") == [old]  # as it came
    same = lambda r: ph.canonical(ph.converted(r))  # noqa: E731
    assert store.add_record("acts", ph.converted(old), at(2026, 10, 22, 10), same) == "duplicate"  # the same record in 0.5
    amended = {**ph.converted(old), "type": "amended", "terms": {"text": "買兩個陶盆"}, "terms_rev": 2}
    assert store.add_record("acts", amended, at(2026, 10, 22, 10), same) == "new"  # the same id, another type (core section 10)
    agents = {**ph.converted(old), "origin": "agent", "type": "note", "about": {"key": "task-pots-1"}}
    assert store.add_record("acts", agents, at(2026, 10, 22, 10), same) == "new"  # the same id from another origin
    assert [r["ph"] for r in store.records_of("acts", P, "task-pots-1")] == ["0.4", "0.5", "0.5"]


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
