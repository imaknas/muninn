"""Lending a thread through the web app: slash commands and lent threads never
reach a model here, the relay's permission requests wait without a lock and
come back with the user's decision, and the feed applies the runner's events."""

from __future__ import annotations

from datetime import timedelta

import pytest
from starlette.testclient import TestClient

from muninn.adapters.channels.webpush import VapidKeys
from muninn.adapters.web import lease_feed
from muninn.adapters.web.app import create_app
from muninn.adapters.web.devices import DeviceRegistry
from muninn.adapters.web.lease_feed import LeaseFeed
from muninn.core.lease import ChannelLease, PermissionDesk
from muninn.core.ports import RunnerError
from muninn.core.project_messages import ProjectMessages

from conftest import ZH, at, make_harness
from test_lease import FakeRunner, ev


@pytest.fixture
def app(tmp_path):
    h = make_harness(at(2026, 10, 22, 15))
    runner = FakeRunner()
    h.engine.leases = ChannelLease(h.store, h.clock, runner, h.channel, lambda now: False, lambda tid: h.engine.conversation().thread(tid), lambda tid, change: h.engine.conversation().update_thread(tid, change),
                                   share_uncleared=True, words=ZH)
    from muninn.core.command_trust import CommandTrust

    desk = PermissionDesk(h.store, h.clock, h.channel, lambda now: False, h.engine.leases,
                          trust=CommandTrust(h.store, lambda: h.store.permission_requests(pending=False), words=ZH), words=ZH)
    keys = VapidKeys(tmp_path / "secrets" / "vapid.pem")
    keys.ensure()
    devices = DeviceRegistry(tmp_path / "dev.db")
    messages = ProjectMessages(h.store, h.clock, h.engine.handoff_log, words=ZH)
    client = TestClient(create_app(h.engine, keys, "tok", devices, messages=messages, lease_runner=runner, permissions=desk),
                        base_url="https://testserver")
    assert client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "iPhone"}).status_code == 200
    return h, runner, client


def test_attach_and_talk_with_muninn_only_listening_beside_it(app):
    h, runner, client = app
    got = client.post("/api/command", json={"text": "/attach garden"}).json()
    assert got["project"] == "garden" and got["lease_id"]
    h.engine.leases.on_event(ev(got["lease_id"], 1, "assistant", text="repo 裡寫著帳號 123"))
    calls_before = len(h.local.calls) + len(h.cloud.calls)
    h.local.reply = h.cloud.reply = '{"for_muninn": null}'
    r = client.post("/api/command", json={"text": "那份貸款文件寫了什麼?", "thread": got["thread"]}).json()
    assert {k: r[k] for k in ("leased", "lease_id", "thread")} == {"leased": True, "lease_id": got["lease_id"], "thread": got["thread"]}
    assert ("send", "那份貸款文件寫了什麼?") in runner.calls
    [(_, prompt)] = (h.local.calls + h.cloud.calls)[calls_before:]  # only Muninn listening beside it, nothing more
    assert "那份貸款文件寫了什麼?" in prompt and "garden:repo 裡寫著帳號 123" in prompt  # the talk as it went, to read him by
    assert not any("帳號 123" in x["text"] for t in h.store.threads(10) for x in t[1]["turns"])  # never kept in Muninn's
    view = client.get(f"/api/leases?thread={got['thread']}").json()
    assert view["lease"]["project"] == "garden" and [t["role"] for t in view["lease"]["turns"]] == ["session", "user"]
    assert "garden" in view["attachable"]


def test_terminal_and_take_back(app):
    _, runner, client = app
    got = client.post("/api/leases/attach", json={"project": "blog"}).json()
    term = client.post("/api/leases/terminal", json={"thread": got["thread"]}).json()
    assert term["command"].endswith(got["lease_id"])
    assert client.post("/api/command", json={"text": "hi", "thread": got["thread"]}).status_code == 400
    assert client.post("/api/command", json={"text": "/detach", "thread": got["thread"]}).json()["state"] == "detaching"
    assert ("detach", "user") in runner.calls


def test_unknown_slash_commands_are_refused_not_understood(app):
    h, _, client = app
    before = len(h.local.calls)
    assert client.post("/api/command", json={"text": "/remember everything"}).status_code == 400
    assert len(h.local.calls) == before


def test_a_permission_request_round_trip(app):
    h, _, client = app
    lid = client.post("/api/leases/attach", json={"project": "garden"}).json()["lease_id"]
    req = {"v": 2, "id": f"{lid}:tu1", "lease": lid, "project": "garden", "kind": "ask_user", "summary": "A or B",
           "question": {"questions": [{"question": "A or B?"}]}}
    assert client.post("/api/permission-requests", json=req).status_code == 401
    auth = {"Authorization": "Bearer tok"}
    assert client.post("/api/permission-requests", json=req, headers=auth).status_code == 202
    assert client.get(f"/api/permission-requests/{req['id']}?wait=0", headers=auth).status_code == 202
    [card] = client.get("/api/permission-requests/pending").json()["items"]
    assert card["kind"] == "ask_user" and card["question"]["questions"][0]["question"] == "A or B?"
    assert client.post(f"/api/permission-requests/{req['id']}/decision",
                       json={"decision": "allow", "answers": {"A or B?": "B"}}).json() == {"ok": True, "note": "你選了:B"}
    assert [t["role"] for t in h.store.lease_turns(lid)] == ["answer"]  # reads back when the user returns
    got = client.get(f"/api/permission-requests/{req['id']}?wait=25", headers=auth)
    assert got.status_code == 200 and got.json() == {"v": 2, "decision": "allow", "answers": {"A or B?": "B"}}
    # the relay retrying its first post now gets the decision straight away
    assert client.post("/api/permission-requests", json=req, headers=auth).json()["decision"] == "allow"


def test_the_feed_applies_events_and_stops_when_the_runner_forgets_the_lease(app, monkeypatch):
    h, runner, client = app
    monkeypatch.setattr(lease_feed, "RETRY_S", (0,))
    lid = h.engine.leases.attach(None, "garden")["lease_id"]  # not through the route: that starts the app's own feed
    batches = [[ev(lid, 1, "assistant", text="這是 session 的回覆"), ev(lid, 1, "assistant", text="這是 session 的回覆")]]

    def events(lease_id, after_seq):
        if batches:
            yield from batches.pop(0)
            return
        raise RunnerError("not_found")

    runner.events = events
    feed = LeaseFeed(h.store, runner, h.engine.leases, None, lambda fn: fn())
    published = []
    feed.publish = lambda lease_id, e: published.append(e)
    feed._follow(lid)  # returns once the runner no longer knows the lease
    assert [t["text"] for t in h.store.lease_turns(lid) if t["role"] == "session"] == ["這是 session 的回覆"]
    assert len([e for e in published if e.get("type") == "assistant"]) == 1  # the replayed duplicate: once
    assert h.store.lease(lid)["state"] == "detached" and h.store.lease(lid)["error"] == "not_found"
    assert h.engine.leases.lease_for(h.store.lease(lid)["thread"]) is None  # the thread came back


def test_the_open_conversation_gets_the_card_and_the_answer_live(app):
    h, _, client = app
    lid = client.post("/api/leases/attach", json={"project": "garden"}).json()["lease_id"]
    feed = client.app.state.lease_feed
    published = []
    feed.publish = lambda lease_id, e: published.append((lease_id, e)) if e["type"].startswith("permission_") else None
    req = {"v": 2, "id": f"{lid}:tu2", "lease": lid, "project": "garden", "kind": "open_web",
           "summary": "打開論文頁", "target": "https://arxiv.org/abs/1"}
    client.post("/api/permission-requests", json=req, headers={"Authorization": "Bearer tok"})
    client.post(f"/api/permission-requests/{req['id']}/decision", json={"decision": "deny"})
    assert [(x, e["type"]) for x, e in published] == [(lid, "permission_card"), (lid, "permission_answered")]
    assert published[0][1]["card"]["domain"] == "arxiv.org" and published[1][1]["text"] == "拒絕打開 arxiv.org"


def test_home_lists_lent_conversations_and_groups_a_deep_talks_questions(app):
    h, _, client = app
    got = client.post("/api/leases/attach", json={"project": "garden"}).json()
    assert [x["thread"] for x in client.get("/api/leases?active=1").json()["active"]] == [got["thread"]]
    auth = {"Authorization": "Bearer tok"}
    client.post("/api/messages", headers=auth, json={"messages": [
        {"id": "q0", "project": "garden", "type": "question", "text": "要不要加範圍?", "context": f"lease:{got['lease_id']}"},
        {"id": "q1", "project": "blog", "type": "question", "text": "標題用哪個?"}]})
    # a talk's own question posted alongside its handover waits nowhere: the handover keeps it with the talk
    assert [q["id"] for q in client.get("/api/project-questions").json()["items"]] == ["q1"]
    assert client.get(f"/api/leases?lease={got['lease_id']}").json()["transcript"]["project"] == "garden"


def test_an_ideas_sandbox_is_taken_back_to_read_only_or_allowed_again_by_a_button_or_by_saying_so(app):
    h, runner, client = app
    runner.sandboxes = ["idea-4"]
    got = client.post("/api/command", json={"text": "/attach idea-4"}).json()
    assert got["can_work"] is True and got["mode"] == "work"
    back = client.post("/api/command", json={"text": "/discuss", "thread": got["thread"]}).json()
    assert back["mode"] == "discuss" and back["note"] == "改回唯讀"
    assert client.post("/api/leases/work", json={"thread": got["thread"]}).json()["mode"] == "work"
    assert [c for c in runner.calls if c[0] == "mode"] == [("mode", "discuss"), ("mode", "work")]
    garden = client.post("/api/command", json={"text": "/attach garden"}).json()
    assert client.post("/api/command", json={"text": "/work", "thread": garden["thread"]}).status_code == 400


def test_pressing_try_it_out_hands_the_conversation_to_the_ideas_sandbox(app):
    """The user asks to try an idea out: no /attach, no 允許動手; the sandbox starts from what was said."""
    import json

    h, runner, client = app
    runner.sandboxes = ["idea-1"]

    class Sandboxes:
        def create(self, name, purpose):
            return name

    h.engine.sandboxes = Sandboxes()
    h.cloud.reply = h.local.reply = json.dumps({
        "actions": [{"tool": "capture_idea", "args": {"idea": "用照片重建房間的 3D 模型"}}], "reply": "粗略版可行。",
        "suggest": [{"label": "開專案實際試試", "tool": "explore_idea", "args": {}}]}, ensure_ascii=False)
    first = client.post("/api/command", json={"text": "用照片重建房間的 3D 模型,可行嗎?"}).json()
    assert "lent" not in first
    r = client.post("/api/command/act", json={"thread": first["thread"], "index": 0}).json()
    assert r["lent"]["project"] == "idea-1" and r["lent"]["mode"] == "work" and r["lent"]["opened"]
    [opening] = [c[1] for c in runner.calls if c[0] == "send"]
    assert "粗略版可行" in opening
    assert not any(c[0] == "mode" for c in runner.calls)
    client.post("/api/command", json={"text": "這是客廳的照片", "thread": first["thread"]})
    assert runner.calls[-1] == ("send", "這是客廳的照片")  # what follows goes to the sandbox, not to a model here


def test_asking_about_a_project_hands_the_conversation_to_it_without_a_slash_command(app):
    import json

    h, runner, client = app
    h.cloud.reply = h.local.reply = json.dumps({
        "actions": [{"tool": "talk_to_project", "args": {"project": "garden"}}], "reply": "我請 garden 那邊來談。"}, ensure_ascii=False)
    r = client.post("/api/command", json={"text": "花園那個專案的排水做到哪了?"}).json()
    assert r["lent"]["project"] == "garden" and "會自己交回來" in r["lent"]["text"]
    assert any(c[0] == "start" and c[1] == "garden" for c in runner.calls)
    h.cloud.reply = h.local.reply = json.dumps({
        "actions": [{"tool": "talk_to_project", "args": {"project": "secret-work"}}], "reply": ""}, ensure_ascii=False)
    nope = client.post("/api/command", json={"text": "那 secret-work 呢?"}).json()
    assert "可以的有" in nope["lent"]["error"]


def test_a_sentence_for_muninn_in_a_lent_conversation_goes_to_muninn_not_the_session(app):
    import json

    h, runner, client = app
    h.store.save_thread("dentist", {"about": None, "ts": "2026-10-22T14:00:00+08:00",
                                    "turns": [{"role": "user", "text": "明天幾點看牙醫?", "ts": "x"}, {"role": "muninn", "text": "10:30", "ts": "x"}]})
    got = client.post("/api/command", json={"text": "/attach garden"}).json()
    h.local.reply = json.dumps({"topic": "dentist"})
    r = client.post("/api/command", json={"text": "那牙醫要帶健保卡嗎?", "thread": got["thread"]}).json()
    assert not any(c[0] == "send" for c in runner.calls)  # the session never heard it
    assert r["thread"] == "dentist" and r["topic"] == {"title": "明天幾點看牙醫?", "switched": True}
    h.local.reply = json.dumps({"topic": "current"})
    r = client.post("/api/command", json={"text": "第一題先選 A", "thread": got["thread"]}).json()
    assert r["leased"] and ("send", "第一題先選 A") in runner.calls  # a sentence for the session still goes to it


def test_talking_to_muninn_in_another_conversation_gives_the_deep_talk_back(app):
    import json

    h, runner, client = app
    got = client.post("/api/command", json={"text": "/attach garden"}).json()
    h.engine.leases.on_event(ev(got["lease_id"], 1, "turn_end"))
    h.cloud.reply = h.local.reply = json.dumps({"actions": [], "reply": "明天 10:30。"}, ensure_ascii=False)
    from datetime import timedelta

    h.clock.t += timedelta(minutes=1)
    client.post("/api/command", json={"text": "明天幾點看牙醫?", "pinned": True})  # a conversation of its own
    assert h.engine.leases.return_idle() == 1 and ("detach", "user") in runner.calls


def test_coming_back_to_a_talk_given_back_a_moment_ago_goes_on_there(app):
    import json
    from datetime import timedelta

    h, runner, client = app
    got = client.post("/api/command", json={"text": "/attach garden"}).json()
    h.engine.leases.on_event(ev(got["lease_id"], 1, "turn_end"))
    h.clock.t += timedelta(minutes=1)
    h.cloud.reply = h.local.reply = json.dumps({"actions": [], "reply": "明天 10:30。"}, ensure_ascii=False)
    client.post("/api/command", json={"text": "明天幾點看牙醫?", "pinned": True})
    assert h.engine.leases.return_idle() == 1
    h.engine.leases.on_event(ev(got["lease_id"], 2, "handover", handover={"summary": "排水談到一半"}))
    h.engine.leases.on_event(ev(got["lease_id"], 3, "state", state="detached"))
    h.clock.t += timedelta(minutes=2)
    h.cloud.reply = h.local.reply = json.dumps({"actions": [{"tool": "talk_to_project", "args": {"project": "garden"}}],
                                                "reply": "好,接回 garden。"}, ensure_ascii=False)
    r = client.post("/api/command", json={"text": "剛剛排水那個,坡度要多少?", "thread": got["thread"], "pinned": True}).json()
    assert r["lent"]["resumed"] and "接回剛才跟 garden 的那段" in r["lent"]["text"]  # Muninn went into it, as any time
    assert ("start", "garden", got["thread"], True) in [c[:4] for c in runner.calls]
    assert "剛剛排水那個,坡度要多少?" in [c[1] for c in runner.calls if c[0] == "send"][-1]  # it hears what went into it
    feed = client.get("/api/conversation").json()["items"]
    [entry] = [x for th in feed for x in th["turns"] if x.get("note")]
    assert "garden" in entry["text"]  # where it began, kept in the conversation


def test_an_experiment_opened_with_the_reply_says_where_the_next_words_go(app):
    import json

    h, runner, client = app
    runner.sandboxes = ["idea-1"]

    class Sandboxes:
        def create(self, name, purpose):
            return name

    h.engine.sandboxes = Sandboxes()
    n = h.engine.ideas.capture("用照片量房間尺寸")[1]
    h.cloud.reply = h.local.reply = json.dumps({"actions": [], "reply": "要不要用客廳的照片試試?", "next": "experiment"},
                                                ensure_ascii=False)
    r = client.post("/api/command", json={"text": "先看量得準不準", "about": {"kind": "idea", "id": str(n)}}).json()
    assert r["lent"]["project"] == f"idea-{n}"
    assert r["lent"]["text"].startswith(f"開了 idea-{n} 在試:接下來你說的會直接給它")  # whatever the reply asked


def test_going_into_a_talk_while_it_writes_its_handover_goes_on_once_it_has(app, monkeypatch):
    import json
    from datetime import timedelta

    h, runner, client = app
    got = client.post("/api/command", json={"text": "/attach garden"}).json()
    h.engine.leases.on_event(ev(got["lease_id"], 1, "turn_end"))
    h.clock.t += timedelta(minutes=1)
    h.cloud.reply = h.local.reply = json.dumps({"actions": [], "reply": "明天 10:30。"}, ensure_ascii=False)
    client.post("/api/command", json={"text": "明天幾點看牙醫?", "pinned": True})
    h.engine.leases.return_idle()
    assert h.store.lease(got["lease_id"])["state"] == "detaching"
    h.cloud.reply = h.local.reply = json.dumps({"actions": [{"tool": "talk_to_project", "args": {"project": "garden"}}],
                                                "reply": "好。"}, ensure_ascii=False)
    r = client.post("/api/command", json={"text": "坡度要多少?", "thread": got["thread"], "pinned": True}).json()
    assert r["lent"]["state"] == "detaching" and "寫完上一段的交接" in r["lent"]["text"]  # Muninn's judgment, the one entry
    assert not [c for c in runner.calls if c[0] == "send" and "坡度要多少?" in c[1]]  # waits for the handover

    monkeypatch.setattr(lease_feed, "RETRY_S", (0,))
    batches = [[ev(got["lease_id"], 2, "handover", handover={"summary": "排水談到一半"}),
                ev(got["lease_id"], 3, "state", state="detached")]]

    def events(lease_id, after_seq):
        if batches:
            yield from batches.pop(0)
            return
        raise RunnerError("not_found")

    runner.events = events
    feed = LeaseFeed(h.store, runner, h.engine.leases, None, lambda fn: fn())
    feed.publish = lambda lease_id, e: None
    feed._follow(got["lease_id"])
    assert ("start", "garden", got["thread"], True) in [c[:4] for c in runner.calls]  # the same session, resumed
    assert any(c[0] == "send" and "坡度要多少?" in c[1] for c in runner.calls)


def test_pressing_a_button_on_something_muninn_asked_does_not_leave_the_deep_talk(app):
    from datetime import timedelta

    h, runner, client = app
    got = client.post("/api/command", json={"text": "/attach garden"}).json()
    h.engine.leases.on_event(ev(got["lease_id"], 1, "turn_end"))
    h.clock.t += timedelta(minutes=1)
    tid = h.engine.conversation().speak(None, "09:35–13:10 電腦沒動,算哪個目標?",
                                        [{"label": "不算(休息、私事)", "tool": "today", "args": {}}])
    assert client.post("/api/command/act", json={"thread": tid, "index": 0}).status_code == 200
    assert h.engine.leases.return_idle() == 0 and not any(c[0] == "detach" for c in runner.calls)


def test_the_open_screens_are_told_when_what_they_show_changed_and_only_then(app):
    import asyncio
    import json

    h, runner, client = app
    screens = client.app.state.screens
    told = []

    async def look():
        q = screens.subscribe()
        screens.check()  # the first look: what is shown now is what the screens were drawn from
        assert q.empty()
        h.cloud.reply = h.local.reply = json.dumps({"actions": [], "reply": "明天 10:30。"}, ensure_ascii=False)
        await asyncio.to_thread(client.post, "/api/command", json={"text": "明天幾點看牙醫?", "pinned": True})
        told.append(screens.check())
        told.append(screens.check())  # nothing written since: not even worked out again
        got = client.post("/api/command", json={"text": "/attach garden"}).json()
        told.append(screens.check())
        h.engine.leases.on_event(ev(got["lease_id"], 1, "text_delta", text="排水⋯"))
        told.append(screens.check())  # a deep talk streaming: written, but nothing the screens show changed
        await asyncio.sleep(0)
        return q.qsize()

    assert asyncio.run(look()) == 2 and told == [True, False, True, False]


def test_answering_with_one_button_takes_the_other_options_away(app):
    h, runner, client = app
    tid = h.engine.conversation().speak(None, "09:35–13:10 電腦沒動,算哪個目標?", [
        {"label": "求職", "tool": "capture_idea", "args": {"idea": "a"}},
        {"label": "健身", "tool": "capture_idea", "args": {"idea": "b"}},
        {"label": "不算(休息、私事)", "tool": "capture_idea", "args": {"idea": "c"}}])
    r = client.post("/api/command/act", json={"thread": tid, "index": 2}).json()
    assert r["suggest"] == []
    [t] = [x for x in client.get("/api/conversation").json()["items"] if x["thread"] == tid]
    assert t["suggest"] == []  # no buttons left in the stream either


def test_a_v2_permission_request_says_what_it_is_by_kind_and_is_answered_in_v2(app):
    h, _, client = app
    lid = client.post("/api/leases/attach", json={"project": "garden"}).json()["lease_id"]
    auth = {"Authorization": "Bearer tok"}
    q = {"v": 2, "id": f"{lid}:q1", "lease": lid, "project": "garden", "kind": "ask_user", "summary": "問你要測哪些房間",
         "question": {"questions": [{"question": "要測哪些房間?", "multi": True,
                                     "options": [{"label": "臥室", "description": "有實測尺寸"}, {"label": "客廳"}]}]},
         "detail": {"tool": "whatever-the-agent-calls-it"}}
    pending = client.post("/api/permission-requests", json=q, headers=auth)
    assert pending.status_code == 202 and pending.json() == {"v": 2, "id": q["id"], "pending": True}
    web = {"v": 2, "id": f"{lid}:w1", "lease": lid, "project": "garden", "kind": "open_web",
           "summary": "打開 arxiv 的論文頁", "target": "https://arxiv.org/abs/2501.01234"}
    client.post("/api/permission-requests", json=web, headers=auth)
    cards = {c["id"]: c for c in client.get("/api/permission-requests/pending").json()["items"]}
    assert cards[q["id"]]["question"]["questions"][0]["multi"] is True
    assert cards[web["id"]]["url"] == "https://arxiv.org/abs/2501.01234" and cards[web["id"]]["domain"] == "arxiv.org"
    note = client.post(f"/api/permission-requests/{q['id']}/decision",
                       json={"decision": "allow", "answers": {"要測哪些房間?": ["臥室", "客廳"]}}).json()["note"]
    assert note == "你選了:臥室、客廳"
    got = client.get(f"/api/permission-requests/{q['id']}?wait=0", headers=auth).json()
    assert got == {"v": 2, "decision": "allow", "answers": {"要測哪些房間?": ["臥室", "客廳"]}}


def test_going_on_in_a_terminal_says_where_as_the_runner_puts_it(app):
    h, runner, client = app
    got = client.post("/api/command", json={"text": "/attach garden"}).json()
    r = client.post("/api/leases/terminal", json={"thread": got["thread"]}).json()
    assert r["command"].startswith("resume-session") and r["hint"] == "在專案那台電腦的終端機貼上"



def test_a_file_the_session_showed_is_served_to_its_own_talk_only_and_said_gone_once_expired(app):
    h, runner, client = app
    got = client.post("/api/command", json={"text": "/attach garden"}).json()
    lid, fid = got["lease_id"], "0123456789abcdef"
    runner.files = {(lid, fid): (b"\x89PNG fake", "image/png")}
    h.engine.leases.on_event(ev(lid, 1, "file", id=fid, path="results/plan.png", name="plan.png", mime="image/png", size=10))
    r = client.get(f"/api/leases/{lid}/files/{fid}?thread={got['thread']}")
    assert r.status_code == 200 and r.content == b"\x89PNG fake" and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["content-disposition"] == "inline"
    assert client.get(f"/api/leases/{lid}/files/{fid}?thread=someone-else").status_code == 404  # not another conversation's
    assert client.get(f"/api/leases/{lid}/files/fedcba9876543210?thread={got['thread']}").status_code == 404  # never shown here
    runner.files = {}  # not there for a moment (still being written, the runner restarting): asked again, never "expired"
    later = client.get(f"/api/leases/{lid}/files/{fid}?thread={got['thread']}")
    assert later.status_code == 503 and later.headers["retry-after"] == "2" and "過期" not in later.text
    h.clock.t += timedelta(days=31)  # past the days it is kept
    gone = client.get(f"/api/leases/{lid}/files/{fid}?thread={got['thread']}")
    assert gone.status_code == 410 and "檔案已經過期" in gone.text
    view = client.get(f"/api/leases?thread={got['thread']}").json()["lease"]["turns"]
    assert [t["files"][0]["id"] for t in view if t["role"] == "file"] == [fid]  # shown again after a reload


def test_a_kind_of_command_allowed_before_is_answered_at_once_and_said_in_the_talk(app):
    h, _, client = app
    lid = client.post("/api/leases/attach", json={"project": "garden"}).json()["lease_id"]
    auth = {"Authorization": "Bearer tok"}
    run = lambda n, script: {"v": 2, "id": f"{lid}:r{n}", "lease": lid, "project": "garden", "kind": "run_command",  # noqa: E731
                             "summary": "跑 python", "target": f"python3 -I - <<'EOF'\nprint({script!r})\nEOF"}
    assert client.post("/api/permission-requests", json=run(1, "one"), headers=auth).status_code == 202  # never seen: asked
    client.post(f"/api/permission-requests/{lid}:r1/decision", json={"decision": "allow"})
    seen = []
    app_feed = client.app.state.lease_feed
    app_feed.publish, real = (lambda lease_id, ev: (seen.append(ev), real(lease_id, ev))[1]), app_feed.publish
    again = client.post("/api/permission-requests", json=run(2, "two"), headers=auth)
    assert again.status_code == 200 and again.json()["decision"] == "allow"  # the relay goes on, nobody asked
    [said] = [e for e in seen if e["type"] == "permission_answered"]
    assert "自動允許跑" in said["text"]
    assert client.get("/api/permissions/auto-commands").json() == {"on": True}
    assert client.post("/api/permissions/auto-commands", json={"on": False}).json() == {"on": False}
    assert client.post("/api/permission-requests", json=run(3, "three"), headers=auth).status_code == 202  # turned off: asked


def test_what_went_wrong_on_a_screen_and_what_the_server_refused_are_logged(app, caplog):
    import logging

    _, _, client = app
    with caplog.at_level(logging.WARNING, logger="muninn.adapters.web.app"):
        assert client.post("/api/client-error", json={"message": "x is undefined", "where": "#item/a:1"}).json() == {"ok": True}
        assert client.post("/api/inbox/read", json={"ids": ["not a number"]}).status_code == 400
    said = caplog.text
    assert "screen error on iPhone: x is undefined (at #item/a:1)" in said
    assert "POST /api/inbox/read refused" in said


def test_saying_the_talk_is_done_lets_it_wrap_up_then_gives_it_back(app):
    import json

    h, runner, client = app
    got = client.post("/api/command", json={"text": "/attach garden"}).json()
    h.engine.leases.on_event(ev(got["lease_id"], 1, "turn_end"))
    h.local.reply = h.cloud.reply = json.dumps({"topic": "current", "for_muninn": None, "end": True})
    r = client.post("/api/command", json={"text": "今天先這樣 收回", "thread": got["thread"]}).json()
    assert r["leased"] and ("send", "今天先這樣 收回") in runner.calls  # the session hears it and wraps up
    assert h.engine.leases.return_idle() == 0  # not cut off while it writes its last answer
    h.engine.leases.on_event(ev(got["lease_id"], 2, "turn_end"))
    assert h.engine.leases.return_idle() == 1 and ("detach", "user") in runner.calls
    assert h.store.lease(got["lease_id"])["end_reason"] == "user"


def test_the_talk_shows_what_its_session_was_told_and_the_stream_keeps_muninns_side_lines_in_the_talk(app):
    import json

    h, runner, client = app
    got = client.post("/api/command", json={"text": "/attach garden"}).json()
    lid, tid = got["lease_id"], got["thread"]
    h.engine.leases.on_event(ev(lid, 1, "turn_end"))
    queue = [{"topic": "current", "for_muninn": "沙盒最少要放行哪些網站"},
             {"actions": [], "reply": "先開 seeds.example 就夠。", "for_project": "沙盒先只放行 seeds.example。"}]

    def complete(system, prompt, *, json_mode=False):
        if len(queue) == 1:
            h.engine.leases.on_event(ev(lid, 2, "turn_end"))  # the session answered him while Muninn thought
        return json.dumps(queue.pop(0), ensure_ascii=False) if queue else '{"actions": [], "reply": ""}'

    h.local.complete = h.cloud.complete = complete
    client.post("/api/command", json={"text": "順便問 Muninn,沙盒最少要放行哪些網站", "thread": tid})
    view = client.get(f"/api/leases?thread={tid}").json()["lease"]
    assert [(t["role"], t["text"]) for t in view["turns"]][-2:] == [("muninn", "先開 seeds.example 就夠。"),
                                                                   ("relay", "沙盒先只放行 seeds.example。")]
    client.post("/api/command", json={"text": "/detach", "thread": tid})
    h.engine.leases.on_event(ev(lid, 3, "handover", handover={"summary": "談了沙盒"}))
    h.engine.leases.on_event(ev(lid, 4, "state", state="detached"))
    assert any(x.get("beside") == lid for x in h.engine.conversation().thread(tid)["turns"])  # Muninn remembers it
    [shown] = [th for th in client.get("/api/conversation").json()["items"] if th["thread"] == tid]
    assert not any("我在旁邊說了" in x["text"] for x in shown["turns"])  # already in the folded talk: not twice


def test_each_thing_muninn_brings_up_keeps_its_own_buttons_until_it_is_answered(app):
    from datetime import timedelta

    h, runner, client = app
    conv = h.engine.conversation()
    tid = conv.speak(None, "上線了:「澆水摘要」", [{"label": "還沒好", "tool": "capture_idea", "args": {"idea": "a"}}])
    h.clock.t += timedelta(seconds=1)
    conv.speak(None, "上線了:「草圖直接看」", [{"label": "還沒好", "tool": "capture_idea", "args": {"idea": "b"}}], thread=tid)
    [t] = [x for x in client.get("/api/conversation").json()["items"] if x["thread"] == tid]
    first, second = (x["ts"] for x in t["turns"])
    assert [(s["index"], s["turn"]) for s in t["suggest"]] == [(0, first), (1, second)]  # each under its own line
    client.post("/api/command/act", json={"thread": tid, "index": 0})
    [t] = [x for x in client.get("/api/conversation").json()["items"] if x["thread"] == tid]
    assert [(s["label"], s["turn"]) for s in t["suggest"]] == [("還沒好", second)]  # the other line's stays
