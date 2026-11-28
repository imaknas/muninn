"""Muninn as the server of Principal Handoff Transport 0.1: records sent are answered one by one, a project's log is
read from the beginning or an opaque cursor with every origin in it, acknowledgements only mark where a project
resumes, and a settlement is sent once it enters the log (an undo after that is a new record)."""

from __future__ import annotations

import time
from datetime import timedelta

import pytest

from muninn.core import ph
from muninn.core.handoff_records import LOGGED_THROUGH
from muninn.core.transport import BACKFILLED, Credential, TransportError

from conftest import at, make_harness

P = "garden"
AGENT = "project:garden"
RELAY = Credential("platform")


def rec(type_, key, ext="acts/0.6", id_=None, origin="requester", by=AGENT, **more):
    return {"ph": ext.split("/")[1], "ext": ext, "id": id_ or f"{type_[0]}:{key}", "type": type_, "origin": origin, "by": by,
            "key": key, "project": P, "ts": "2026-10-22T09:00:00Z", **more}


def raised(key, text, **more):
    return rec("raised", key, id_=f"r:{key}", terms={"text": text}, terms_rev=1, **more)


@pytest.fixture
def h():
    return make_harness(at(2026, 10, 22, 10))


def read_all(h, cred=RELAY, as_=AGENT, project=P, after=None):
    got, out = {"more": True, "cursor": after}, []
    while got["more"]:
        got = h.engine.transport.fetch(cred, project, got["cursor"], as_, 2)
        out += got["records"]
    return out, got["cursor"]


def test_each_record_is_answered_alone_and_sending_again_is_a_duplicate(h):
    t = h.engine.transport
    got = t.send(RELAY, [raised("task-pots-1", "買陶盆"), rec("settled", "task-x", origin="principal"), "nope",
                         {**raised("task-pots-2", "量陽台"), "ts": "not a time"}])
    assert [g["status"] for g in got] == ["accepted", "not_authorised", "invalid", "invalid"]
    assert got[0]["id"] == "r:task-pots-1" and got[3]["errors"][0]["code"] == "invalid" and got[3]["errors"][0]["message"]
    assert t.send(RELAY, [raised("task-pots-1", "買陶盆")]) == [{"status": "duplicate", "id": "r:task-pots-1"}]
    assert h.engine.tasks.get("task-pots-1")["title"] == "買陶盆"


def test_an_id_sent_again_with_other_content_is_accepted_and_both_are_in_the_log(h):
    t = h.engine.transport
    t.send(RELAY, [raised("task-pots-1", "買陶盆")])
    assert t.send(RELAY, [{**raised("task-pots-1", "買陶盆"), "ts": "2026-10-22T09:05:00Z"}])[0]["status"] == "accepted"
    records, _ = read_all(h)
    assert [r["ts"] for r in records] == ["2026-10-22T09:00:00Z", "2026-10-22T09:05:00Z"]


def test_a_record_muninn_does_not_implement_is_kept_stored_and_in_the_log(h):
    t = h.engine.transport
    future = {"ph": "0.7", "ext": "acts/0.7", "id": "x:1", "origin": "requester", "by": AGENT, "project": P, "novel": [1]}
    relation_05 = rec("related", "task-pots-1", ext="acts/0.5", id_="rel:1", relation={"kind": "derived_from",
                      "target": {"ext": "tickets/0.5", "project": "has space", "key": "k"}, "active": True})
    got = t.send(RELAY, [future, relation_05])
    assert [(g["status"], g.get("reason")) for g in got] == [("kept", "not_implemented"), ("kept", "no_form")]
    records, _ = read_all(h)
    assert future in records and relation_05 in records  # as they were sent, unknown members and all
    assert t.send(RELAY, [future])[0]["status"] == "duplicate"


def test_a_credential_speaks_only_for_its_parties_and_never_for_the_principal_side(h):
    t = h.engine.transport
    narrow = Credential("garden agent", frozenset({AGENT}))
    other = raised("task-pots-1", "買陶盆", by="project:blog")
    muninn = raised("task-pots-2", "量陽台", by="muninn")
    assert [g["status"] for g in t.send(narrow, [other, raised("task-pots-3", "澆水")])] == ["not_authorised", "accepted"]
    assert t.send(RELAY, [muninn])[0]["status"] == "not_authorised"
    assert t.fetch(narrow, P, None, None, None)["records"]  # one party: no `as` needed
    with pytest.raises(TransportError) as e:
        t.fetch(RELAY, P, None, None, None)
    assert e.value.code == "bad_parameter"  # a relay names the party it fetches for
    for who in ("project:blog", "muninn", h.engine.parties.principal):
        with pytest.raises(TransportError) as e:
            t.fetch(narrow if who == "project:blog" else RELAY, P, None, who, None)
        assert e.value.code == "no_role"


def test_the_log_is_read_from_the_beginning_in_order_with_every_origin_and_the_cursor_is_opaque(h):
    t = h.engine.transport
    t.send(RELAY, [raised("task-pots-1", "買陶盆"), raised("task-pots-2", "量陽台")])
    h.engine.tasks.close("task-pots-1", why="買好了")
    records, cursor = read_all(h)
    assert [(r["origin"], r["type"], r["key"]) for r in records] == [
        ("requester", "raised", "task-pots-1"), ("requester", "raised", "task-pots-2"), ("principal", "settled", "task-pots-1")]
    assert isinstance(cursor, str) and t.fetch(RELAY, P, cursor, AGENT, None) == {"records": [], "cursor": cursor, "more": False, "acked": None}
    assert t.fetch(RELAY, "blog", None, AGENT, None) == {"records": [], "cursor": None, "more": False, "acked": None}
    with pytest.raises(TransportError) as e:
        t.fetch(RELAY, "blog", cursor, AGENT, None)  # a position of another project's log
    assert e.value.code == "unknown_cursor"


def test_an_acknowledgement_is_kept_per_party_never_goes_back_and_changes_nothing(h):
    t = h.engine.transport
    t.send(RELAY, [raised("task-pots-1", "買陶盆"), raised("task-pots-2", "量陽台")])
    first = t.fetch(RELAY, P, None, AGENT, 1)
    last = t.fetch(RELAY, P, first["cursor"], AGENT, None)["cursor"]
    assert t.ack(RELAY, P, last, AGENT) == last and t.ack(RELAY, P, first["cursor"], AGENT) == last
    assert t.fetch(RELAY, P, None, AGENT, None)["acked"] == last
    assert t.fetch(RELAY, P, None, "registrar:hub", None)["acked"] is None
    with pytest.raises(TransportError) as e:
        t.ack(RELAY, P, "g1.999999", AGENT)
    assert e.value.code == "unknown_cursor"


def test_a_close_not_held_is_sent_as_it_is_made_and_its_undo_is_a_reopening_on_both_apis(h):
    t = h.engine.transport
    t.send(RELAY, [raised("task-pots-1", "買陶盆")])
    r = h.engine.tasks.close("task-pots-1", why="買好了")
    assert h.engine.undo_action(r.id)  # read by no one yet: it was sent all the same
    records, _ = read_all(h)
    settled, reopened = records[1], records[2]
    assert (settled["type"], reopened["type"], reopened["supersedes"]) == ("settled", "reopened", [settled["id"]])
    assert [x["type"] for x in h.engine.records_out.since(P, 0)[0]] == ["settled", "reopened"]


def test_a_close_held_for_an_undo_enters_the_log_when_it_comes_due_and_one_taken_back_never(h):
    t = h.engine.transport
    t.send(RELAY, [raised("task-pots-1", "買陶盆"), raised("task-pots-2", "量陽台")])
    first = h.engine.tasks.close("task-pots-1", by="evidence", why="他說買好了")
    h.engine.tasks.close("task-pots-2", by="evidence", why="量好了")
    assert [r["type"] for r in read_all(h)[0]] == ["raised", "raised"]
    assert h.engine.undo_action(first.id)  # taken back while held: never sent
    h.clock.t += timedelta(hours=13)
    assert [(r["type"], r["key"]) for r in read_all(h)[0][2:]] == [("settled", "task-pots-2")]


def test_a_handoff_sent_as_a_record_is_never_rewritten_a_stronger_one_follows_it(h):
    store = h.engine.store
    first = h.engine.handoff_log.add(project=P, item="買陶盆", fact="他買了", effect="relevant", reply_to="k")
    store.log_append(P, {"id": "x"}, "d1", h.clock.now(), own=True, handoff=first["id"])
    second = h.engine.handoff_log.add(project=P, item="買陶盆", fact="他買了", effect="resolves", reply_to="k")
    assert second["id"] != first["id"] and h.engine.handoff_log.get(first["id"])["effect"] == "relevant"
    held = (h.clock.now() + timedelta(hours=1)).isoformat()  # not sent while held
    unsent = h.engine.handoff_log.add(project=P, item="量陽台", fact="x", effect="relevant", reply_to="j", not_before=held)
    assert h.engine.handoff_log.add(project=P, item="量陽台", fact="x", effect="resolves", reply_to="j")["id"] == unsent["id"]
    assert h.engine.handoff_log.add(project=P, item="買陶盆", fact="他買了", effect="resolves", reply_to="k")["id"] == second["id"]


def test_what_was_held_before_the_log_enters_it_once_in_time_order(h):
    t = h.engine.transport
    h.engine.records_in.receive([raised("task-pots-1", "買陶盆")])
    h.clock.t += timedelta(minutes=5)
    h.engine.tasks.close("task-pots-1", why="買好了")
    before, _ = read_all(h)
    h.engine.store._x("DELETE FROM ph_log")  # as a database from before the log
    for key in (BACKFILLED, LOGGED_THROUGH):
        h.engine.store.set_state(key, None)
    t.backfill()
    t.backfill()
    assert read_all(h)[0] == before


def test_a_new_generation_makes_every_cursor_unknown_and_drops_the_acknowledgements(h):
    t = h.engine.transport
    t.send(RELAY, [raised("task-pots-1", "買陶盆")])
    cursor = t.fetch(RELAY, P, None, AGENT, None)["cursor"]
    t.ack(RELAY, P, cursor, AGENT)
    t.new_generation()
    with pytest.raises(TransportError) as e:
        t.fetch(RELAY, P, cursor, AGENT, None)
    assert e.value.code == "unknown_cursor"
    got = t.fetch(RELAY, P, None, AGENT, None)
    assert got["acked"] is None and got["cursor"] != cursor and len(got["records"]) == 1


def test_muninns_records_are_told_in_the_version_configured_and_the_others_as_sent(h):
    t = h.engine.transport
    t.send(RELAY, [raised("task-pots-1", "買陶盆")])
    h.engine.tasks.close("task-pots-1", why="買好了")
    h.engine.handoff_versions.set(P, ph.ACTS, ph.OLDER)
    records, _ = read_all(h)
    assert records[0]["ext"] == "acts/0.6" and records[1]["ext"] == "acts/0.5"


# the HTTP mapping

@pytest.fixture
def web(tmp_path, h):
    from starlette.testclient import TestClient

    from muninn.adapters.channels.webpush import VapidKeys
    from muninn.adapters.web.app import create_app
    from muninn.adapters.web.devices import DeviceRegistry

    keys = VapidKeys(tmp_path / "secrets" / "vapid.pem")
    keys.ensure()
    app = create_app(h.engine, keys, "tok", DeviceRegistry(tmp_path / "dev.db"), public_url="https://muninn.example:8444")
    client = TestClient(app, base_url="https://muninn.example:8444")
    client.headers.update({"Authorization": "Bearer tok"})
    return client


def test_http_send_fetch_and_ack(web):
    r = web.post("/ph/records", json={"records": [raised("task-pots-1", "買陶盆")]})
    assert r.status_code == 200 and r.json() == {"results": [{"status": "accepted", "id": "r:task-pots-1"}]}
    got = web.get(f"/ph/records?project={P}&as={AGENT}").json()
    assert [x["id"] for x in got["records"]] == ["r:task-pots-1"] and got["more"] is False and got["acked"] is None
    assert web.post("/ph/records/ack", json={"project": P, "through": got["cursor"], "as": AGENT}).json() == {"acked": got["cursor"]}
    assert "access-control-allow-origin" not in r.headers


def test_http_errors_are_one_envelope(web):
    def code(r):
        return r.status_code, r.json()["errors"][0]["code"]

    assert code(web.get(f"/ph/records?project={P}&as={AGENT}", headers={"Authorization": "Bearer nope"})) == (401, "unauthenticated")
    assert code(web.get(f"/ph/records?project={P}&as={AGENT}&as={AGENT}")) == (400, "bad_parameter")
    assert code(web.get(f"/ph/records?project={P}&as={AGENT}&since=1")) == (400, "bad_parameter")
    assert code(web.get(f"/ph/records?project={P}")) == (400, "bad_parameter")
    assert code(web.get(f"/ph/records?project={P}&as={AGENT}&wait=61")) == (400, "bad_parameter")
    assert code(web.get(f"/ph/records?project={P}&as={AGENT}&after=g9.1")) == (400, "unknown_cursor")
    assert code(web.get(f"/ph/records?project={P}&as=muninn")) == (403, "no_role")
    assert code(web.post("/ph/records", json={"records": []})) == (400, "malformed")
    assert code(web.post("/ph/records", content=b"{", headers={"Content-Type": "application/json"})) == (400, "malformed")
    assert code(web.post("/ph/records", json={"records": [raised("a", "b")] * 501})) == (413, "too_large")


def test_http_refuses_a_host_other_than_the_base_urls(web):
    r = web.get(f"/ph/records?project={P}&as={AGENT}", headers={"Host": "127.0.0.1:18795"})
    assert r.status_code == 421 and r.json()["errors"][0]["code"] == "misdirected"


def test_http_a_fetch_with_wait_holds_until_the_time_passes(web):
    cursor = web.post("/ph/records", json={"records": [raised("task-pots-1", "買陶盆")]}) and \
        web.get(f"/ph/records?project={P}&as={AGENT}").json()["cursor"]
    began = time.monotonic()
    got = web.get(f"/ph/records?project={P}&as={AGENT}&after={cursor}&wait=1").json()
    assert got["records"] == [] and got["cursor"] == cursor and time.monotonic() - began >= 0.9
