"""Muninn beside a deep talk: a sentence said in a lent conversation is judged once, for where it goes and what in
it is for Muninn; it goes to the session (told what Muninn took), and Muninn does its part in the background with
the conversation's own history, said in one line beside the talk. The session's replies are never read for it,
and doing it is not the user moving on, so the talk is not given back."""

from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from muninn.adapters.channels.webpush import VapidKeys
from muninn.adapters.web.app import create_app
from muninn.adapters.web.devices import DeviceRegistry
from muninn.core.lease import ChannelLease

from conftest import ZH, at, make_harness
from test_lease import FakeRunner, ev


@pytest.fixture
def app(tmp_path):
    h = make_harness(at(2026, 10, 25, 10))
    runner = FakeRunner()
    h.engine.leases = ChannelLease(h.store, h.clock, runner, h.channel, lambda now: False,
                                   lambda tid: h.engine.conversation().thread(tid), lambda tid, change: h.engine.conversation().update_thread(tid, change), words=ZH)
    keys = VapidKeys(tmp_path / "secrets" / "vapid.pem")
    keys.ensure()
    devices = DeviceRegistry(tmp_path / "dev.db")
    client = TestClient(create_app(h.engine, keys, "tok", devices, lease_runner=runner), base_url="https://testserver")
    assert client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "iPhone"}).status_code == 200
    h.store.save_thread("talk", {"about": None, "ts": "2026-10-25T09:00", "turns": [
        {"role": "user", "text": "花園的排水要重做", "ts": "2026-10-25T09:00", "cloud_ok": True},
        {"role": "muninn", "text": "好,交給 garden 談", "ts": "2026-10-25T09:00"}]})
    lid = h.engine.leases.attach("talk", "garden")["lease_id"]
    h.engine.leases.on_event(ev(lid, 1, "assistant", text="排水管要先挖 30 公分"))
    return h, runner, client, lid


def answers(h, *replies):
    queue = [json.dumps(r, ensure_ascii=False) for r in replies]

    def complete(system, prompt, *, json_mode=False):
        h.local.calls.append((system, prompt))
        return queue.pop(0) if queue else '{"actions": [], "reply": ""}'

    h.local.complete = complete


def test_what_is_for_muninn_is_judged_once_done_beside_the_talk_and_the_session_told(app):
    h, runner, client, lid = app
    answers(h, {"topic": "current", "for_muninn": "提醒我明天打給水電行"},
            {"actions": [{"tool": "add_task", "args": {"title": "打給水電行", "due": "2026-10-26"}}], "reply": "好,明天提醒你。"})
    r = client.post("/api/command", json={"text": "好,先挖。順便提醒我明天打給水電行", "thread": "talk"}).json()
    assert r["leased"] and not r["topic"]["switched"]
    system, judged = h.local.calls[0]
    assert "garden" in system and "順便提醒我明天打給水電行" in judged and "garden:排水管要先挖 30 公分" in judged  # the talk as it went
    assert runner.calls[-1] == ("send", "好,先挖。順便提醒我明天打給水電行\n\n(這句裡「提醒我明天打給水電行」Muninn 會處理,你不用做。)")
    assert "花園的排水要重做" in h.local.calls[1][1]  # done with the conversation's own history
    assert len(h.local.calls) == 2  # one judgment, one round for Muninn's part: nothing judged twice
    beside = [x for x in h.store.lease_turns(lid) if x["role"] == "muninn"]
    assert len(beside) == 1 and "打給水電行" in beside[0]["text"] and "好,明天提醒你" not in beside[0]["text"]  # what was done, not its sentence
    user = [x["text"] for x in h.store.lease_turns(lid) if x["role"] == "user"]
    assert user[-1] == "好,先挖。順便提醒我明天打給水電行"  # the note to the session is not shown as his words
    assert h.store.get_state("talk:last_user") is None  # not a round elsewhere: the talk is not given back for it


def test_what_is_all_the_projects_goes_to_it_alone(app):
    h, runner, client, lid = app
    answers(h, {"topic": "current", "for_muninn": None})
    client.post("/api/command", json={"text": "排水管要挖多深?", "thread": "talk"})
    assert runner.calls[-1] == ("send", "排水管要挖多深?") and len(h.local.calls) == 1
    assert not any(x["role"] == "muninn" for x in h.store.lease_turns(lid))


def test_a_sentence_about_another_conversation_leaves_the_talk_whole(app):
    h, runner, client, lid = app
    h.store.save_thread("dentist", {"about": None, "ts": "2026-10-25T09:30", "turns": [
        {"role": "user", "text": "明天幾點看牙醫?", "ts": "2026-10-25T09:30", "cloud_ok": True}]})
    answers(h, {"topic": "dentist", "for_muninn": "牙醫改後天"}, {"actions": [], "reply": "好,改後天。"})
    r = client.post("/api/command", json={"text": "牙醫改後天", "thread": "talk"}).json()
    assert r["topic"]["switched"] and r["thread"] == "dentist"
    assert not any(c[0] == "send" and "牙醫" in c[1] for c in runner.calls)  # nothing of it to the session


def _went_quiet(h, lid):
    """The talk went quiet and was given back by itself, its handover written."""
    h.engine.leases.on_event(ev(lid, 2, "idle_warning", idle_s=1800))
    h.engine.leases.return_idle()
    h.engine.leases.on_event(ev(lid, 3, "handover", handover={"summary": "談到排水要先挖"}))
    h.engine.leases.on_event(ev(lid, 4, "state", state="detached"))
    assert h.engine.leases.lease_for("talk") is None


def test_coming_back_to_a_talk_that_went_quiet_is_muninns_to_go_into_again_and_its_part_done(app):
    h, runner, client, lid = app
    _went_quiet(h, lid)
    h.engine.leases._share_uncleared = True  # everything goes to the cloud, as routing "cloud" has it
    from datetime import timedelta

    h.clock.t += timedelta(minutes=20)  # he comes back later
    answers(h, {"actions": [{"tool": "add_task", "args": {"title": "打給水電行", "due": "2026-10-26"}},
                         {"tool": "talk_to_project", "args": {"project": "garden"}}], "reply": "好,明天提醒你。"})
    r = client.post("/api/command", json={"text": "繼續挖。另外提醒我明天打給水電行", "thread": "talk", "pinned": True}).json()
    assert r["lent"]["resumed"] and r["lent"]["lease_id"] == lid  # gone into again, to the same session, as any time
    assert "繼續挖。另外提醒我明天打給水電行" in [c[1] for c in runner.calls if c[0] == "send"][-1]  # it hears it
    assert any(t["title"] == "打給水電行" for t in h.engine.tasks.open())  # Muninn's part, done by Muninn


def test_coming_back_to_a_quiet_talk_with_something_else_entirely_leaves_it_given_back(app):
    h, runner, client, lid = app
    h.store.save_thread("dentist", {"about": None, "ts": "2026-10-25T09:30", "turns": [
        {"role": "user", "text": "牙醫約在週五", "ts": "2026-10-25T09:30", "cloud_ok": True},
        {"role": "muninn", "text": "記下了", "ts": "2026-10-25T09:30"}]})
    _went_quiet(h, lid)
    answers(h, {"topic": "dentist", "for_muninn": None}, {"actions": [], "reply": "週五下午三點。"})
    r = client.post("/api/command", json={"text": "牙醫是幾點?", "thread": "talk"}).json()
    assert r["topic"]["switched"] and r["reply"] == "週五下午三點。"
    assert h.engine.leases.lease_for("talk") is None and not any(c[0] == "start" and c[2] == "talk" and c[3] for c in runner.calls)


def test_what_he_hands_muninn_in_a_talk_is_read_by_the_talk_and_never_sends_the_conversation_elsewhere(app):
    h, runner, client, lid = app
    client.post("/api/command", json={"text": "你可以給我看圖嗎?", "thread": "talk"})  # (judged, sent to the session)
    h.engine.leases.on_event(ev(lid, 2, "assistant", text="我沒有把圖顯示給你的工具,這裡只能傳文字"))
    answers(h, {"topic": "current", "for_muninn": "深談裡專案產生的圖沒辦法給我看"},
            {"actions": [{"tool": "request_feature", "args": {"text": "深談裡專案產生的圖沒辦法給他看"}},
                         {"tool": "talk_to_project", "args": {"project": "garden"}}],
             "reply": "記下了:深談裡看不到專案產生的圖。", "suggest": [{"label": "接回去", "tool": "talk_to_project", "args": {"project": "garden"}}]})
    before = len(h.local.calls)
    client.post("/api/command", json={"text": "你去跟 Muninn 說這個問題", "thread": "talk"})
    judged = h.local.calls[before][1]
    assert "garden:我沒有把圖顯示給你的工具" in judged  # what 「這個問題」 is: what the session just said
    round_for_muninn = json.loads(h.local.calls[before + 1][1])
    assert round_for_muninn["beside_talk"]["project"] == "garden" and any("沒有把圖顯示" in x for x in round_for_muninn["beside_talk"]["lines"])
    asked = [x["text"] for x in h.store.lease_turns(lid) if x["role"] == "muninn"]
    assert asked and asked[-1].startswith("記到需求清單了")  # done beside the talk: what happened, not a sentence about it
    req = h.engine.feature_requests()[-1]
    assert (req["thread"], req["via"]) == ("talk", [f"lease:{lid}"])  # kept with its conversation, and as the talk's content
    assert not [c for c in runner.calls if c[0] == "start" and c[1] != "garden"]  # nothing lent elsewhere
    assert h.engine.leases.lease_for("talk")["lease_id"] == lid  # the talk goes on as it was
    assert not any("沒有把圖顯示" in x["text"] for x in h.engine.conversation().thread("talk")["turns"])  # read, not kept


def _idea_talk(h, runner):
    """An idea talked about with its sandbox: the conversation lent to idea-<n>."""
    upgraded = []
    h.engine.ideas._sandbox = lambda name, purpose: name
    h.engine.ideas._upgrade = lambda name, rename: (upgraded.append((name, rename)), rename or name)[1]
    n = h.engine.ideas.capture("用照片重建房間")[1]
    runner.sandboxes = [f"idea-{n}"]
    h.engine.ideas.explore(n)
    h.store.save_thread("ideatalk", {"about": {"kind": "idea", "id": str(n)}, "ts": "2026-10-25T09:50", "turns": [
        {"role": "user", "text": "試試照片重建", "ts": "2026-10-25T09:50", "cloud_ok": True}]})
    lid = h.engine.leases.attach("ideatalk", f"idea-{n}")["lease_id"]
    return n, lid, upgraded


def test_making_the_idea_a_project_inside_its_own_talk_is_done_by_muninn(app):
    h, runner, client, _ = app
    n, lid, upgraded = _idea_talk(h, runner)
    answers(h, {"topic": "current", "for_muninn": f"把 idea-{n} 升級成專案,叫 shelf-planner"},
            {"actions": [{"tool": "promote_idea", "args": {"idea": n, "name": "shelf-planner"}}], "reply": ""})
    client.post("/api/command", json={"text": "好,把它升級成專案,叫 shelf-planner", "thread": "ideatalk"})
    assert upgraded == [(f"idea-{n}", "shelf-planner")]  # only Muninn can: the sandbox's session cannot reach it
    assert runner.calls[-1] == ("send", f"好,把它升級成專案,叫 shelf-planner\n\n(這句裡「把 idea-{n} 升級成專案,叫 shelf-planner」Muninn 會處理,你不用做。)")
    beside = [x["text"] for x in h.store.lease_turns(lid) if x["role"] == "muninn"]
    assert beside and beside[-1].startswith("升級好了,叫 shelf-planner")
    assert h.store.lease(lid)["project"] == "shelf-planner"  # the talk goes on under its new name
    assert h.engine.ideas.get(n)["status"] == "promoted"


def test_setting_the_idea_aside_inside_its_own_talk_is_done_by_muninn(app):
    h, runner, client, _ = app
    n, lid, _ = _idea_talk(h, runner)
    answers(h, {"topic": "current", "for_muninn": f"idea-{n} 先擱著"},
            {"actions": [{"tool": "settle_idea", "args": {"idea": n, "decision": "shelve", "why": "先擱著"}}], "reply": "好,先擱著。"})
    client.post("/api/command", json={"text": "這個先擱著吧", "thread": "ideatalk"})
    assert h.engine.ideas.get(n)["status"] == "shelved"
    assert any("擱著" in x["text"] for x in h.store.lease_turns(lid) if x["role"] == "muninn")


def test_a_promotion_that_fails_beside_the_talk_says_only_that_it_failed(app):
    from muninn.core.ports import SandboxRefused

    h, runner, client, _ = app
    n, lid, _ = _idea_talk(h, runner)

    def fails(name, rename):
        raise SandboxRefused("failed", detail="upgrade_failed: 登錄不合格")

    h.engine.ideas._upgrade = fails
    answers(h, {"topic": "current", "for_muninn": f"可行的話把 idea-{n} 升級成專案"},
            {"actions": [{"tool": "promote_idea", "args": {"idea": n}}], "reply": "可行,所以我把它升成專案了。接下來等你補施工圖。"})
    client.post("/api/command", json={"text": "總體而言想法是否可行?可行就升級成專案", "thread": "ideatalk"})
    [line] = [x["text"] for x in h.store.lease_turns(lid) if x["role"] == "muninn"]
    assert line.startswith("沒做成:升級成專案失敗了") and "升成專案了" not in line and "施工圖" not in line
    assert "想清楚" not in line  # what happened, never where to talk
    assert h.engine.ideas.get(n).get("status") != "promoted"


def test_beside_the_talk_an_answer_without_anything_done_is_a_sentence_or_two(app):
    h, runner, client, lid = app
    answers(h, {"topic": "current", "for_muninn": "我明天下午有空嗎"},
            {"actions": [], "reply": "明天下午兩點後有空。三點有個會。另外那個專案的排水坡度建議 2%。再補充一點。"})
    client.post("/api/command", json={"text": "順便問,我明天下午有空嗎", "thread": "talk"})
    [line] = [x["text"] for x in h.store.lease_turns(lid) if x["role"] == "muninn"]
    assert line == "明天下午兩點後有空。三點有個會。"


# ── what of Muninn's answer is the project's, told to its session ───────────

SANDBOX = "沙盒先只放行 seeds.example 和 *.seeds.example。"
TOLD = f"(Muninn 剛才在旁邊回他,跟你有關的是:{SANDBOX}你知道就好,不用回。)"


def answered_beside(h, lid, *, meanwhile=lambda: None, reply="先開 seeds.example 和 *.seeds.example 就夠。", end=False):
    """He hands Muninn a question about the sandbox in the talk; `meanwhile`: what happens while Muninn thinks."""
    queue = [json.dumps({"topic": "current", "for_muninn": "沙盒最少要放行哪些網站", "end": end}, ensure_ascii=False),
             json.dumps({"actions": [], "reply": reply, "for_project": SANDBOX}, ensure_ascii=False)]

    def complete(system, prompt, *, json_mode=False):
        h.local.calls.append((system, prompt))
        if len(queue) == 1:
            meanwhile()
        return queue.pop(0) if queue else '{"actions": [], "reply": ""}'

    h.local.complete = complete


def told_to(h):
    shown = []
    h.engine.leases.on_relay(lambda lease_id, e: shown.append((lease_id, e)))
    return shown


def test_what_is_the_projects_in_muninns_answer_is_told_to_the_session_at_once_with_a_new_id(app):
    h, runner, client, lid = app
    h.engine.leases._share_uncleared = True  # everything goes to the cloud, as routing "cloud" has it
    shown = told_to(h)
    # the session answers his words while Muninn still thinks
    answered_beside(h, lid, meanwhile=lambda: h.engine.leases.on_event(ev(lid, 2, "turn_end")))
    said_at = h.store.lease(lid)["last_user_at"]
    client.post("/api/command", json={"text": "我想跟 Muninn 討論沙盒最少要放行哪些網站", "thread": "talk", "client_msg_id": "m-1"})
    assert runner.calls[-1] == ("send", TOLD)  # a message of its own: only the sentence that is the project's
    assert runner.msg_ids[-1] not in ("m-1", runner.msg_ids[-2]) and len(set(runner.msg_ids)) == len(runner.msg_ids)
    turns = h.store.lease_turns(lid)
    assert [t["role"] for t in turns][-3:] == ["user", "muninn", "relay"] and turns[-1]["text"] == SANDBOX
    lease = h.store.lease(lid)
    assert lease["answering"] and lease["last_user_at"] == said_at  # it answers that too; he did not speak
    assert shown == [(lid, {"type": "relay", "text": SANDBOX})]


def test_while_the_session_answers_his_words_what_muninn_tells_it_waits_for_its_turn_to_end(app, monkeypatch):
    from muninn.adapters.web import lease_feed
    from muninn.adapters.web.lease_feed import LeaseFeed

    h, runner, client, lid = app
    h.engine.leases._share_uncleared = True
    shown = told_to(h)
    answered_beside(h, lid)
    client.post("/api/command", json={"text": "我想跟 Muninn 討論沙盒最少要放行哪些網站", "thread": "talk"})
    assert runner.calls[-1][1].startswith("我想跟 Muninn 討論") and shown == []  # not into the middle of its answer
    assert not any(t["role"] == "relay" for t in h.store.lease_turns(lid))

    monkeypatch.setattr(lease_feed, "RETRY_S", (0,))
    feed = LeaseFeed(h.store, runner, h.engine.leases, None, lambda fn: fn())
    feed.publish = lambda lease_id, e: None

    def events(lease_id, after_seq):
        feed.stop()  # one event, then the feed is done
        yield ev(lid, 2, "turn_end")

    runner.events = events
    feed._follow(lid)
    assert runner.calls[-1] == ("send", TOLD) and shown == [(lid, {"type": "relay", "text": SANDBOX})]
    assert h.store.lease_turns(lid)[-1]["role"] == "relay"


def test_what_could_not_be_told_goes_before_his_next_words_and_is_kept_when_that_fails_too(app):
    from muninn.core.ports import RunnerError

    h, runner, client, lid = app
    h.engine.leases._share_uncleared = True

    def session_done_and_runner_down():
        h.engine.leases.on_event(ev(lid, 2, "turn_end"))
        runner.fail = RunnerError("unreachable")

    answered_beside(h, lid, meanwhile=session_done_and_runner_down)
    client.post("/api/command", json={"text": "我想跟 Muninn 討論沙盒最少要放行哪些網站", "thread": "talk"})
    assert not any(t["role"] == "relay" for t in h.store.lease_turns(lid))  # not heard: not shown as told
    answers(h, {"topic": "current", "for_muninn": None})
    assert client.post("/api/command", json={"text": "那就先開這兩個", "thread": "talk"}).status_code == 400  # still down
    runner.fail = None
    answers(h, {"topic": "current", "for_muninn": None})
    client.post("/api/command", json={"text": "那就先開這兩個", "thread": "talk"})
    assert runner.calls[-1] == ("send", f"(Muninn 剛才在旁邊回他,跟你有關的是:{SANDBOX})\n\n那就先開這兩個")  # it came first
    assert [(t["role"], t["text"]) for t in h.store.lease_turns(lid)][-2:] == [("user", "那就先開這兩個"), ("relay", SANDBOX)]  # kept as the screen shows it
    assert h.engine.leases.flush_relays(lid) is False  # told once


def test_two_tries_at_once_tell_the_session_once(app):
    h, runner, client, lid = app
    h.engine.leases._share_uncleared = True
    answered_beside(h, lid)
    client.post("/api/command", json={"text": "我想跟 Muninn 討論沙盒最少要放行哪些網站", "thread": "talk"})  # waits: it is answering
    h.engine.leases.on_event(ev(lid, 2, "turn_end"))
    send, again = runner.send, []

    def sending(lease_id, text, client_msg_id, attachments=()):
        send(lease_id, text, client_msg_id, attachments)
        again.append(h.engine.leases.flush_relays(lease_id))  # the other try, while the first is sending

    runner.send = sending
    assert h.engine.leases.flush_relays(lid) is True and again == [False]
    assert [c for c in runner.calls if c == ("send", TOLD)] == [("send", TOLD)]
    assert [t["role"] for t in h.store.lease_turns(lid)].count("relay") == 1


def test_nothing_is_told_to_the_session_unless_everything_goes_to_the_cloud(app):
    h, runner, client, lid = app  # the fixture's routing keeps uncleared words local
    answered_beside(h, lid, meanwhile=lambda: h.engine.leases.on_event(ev(lid, 2, "turn_end")))
    client.post("/api/command", json={"text": "我想跟 Muninn 討論沙盒最少要放行哪些網站", "thread": "talk"})
    assert not any(c[0] == "send" and "Muninn 剛才" in c[1] for c in runner.calls)
    assert not any(t["role"] == "relay" for t in h.store.lease_turns(lid)) and not h.store.get_state("lease:relay")
    assert any(t["role"] == "muninn" for t in h.store.lease_turns(lid))  # answered beside the talk all the same


def test_a_talk_he_is_done_with_waits_for_muninns_part_and_what_it_tells_the_session(app):
    h, runner, client, lid = app
    h.engine.leases._share_uncleared = True
    given_back = []

    def session_answers_first():
        h.engine.leases.on_event(ev(lid, 2, "turn_end"))  # it wrapped up before Muninn answered
        given_back.append(h.engine.leases.return_idle())

    answered_beside(h, lid, meanwhile=session_answers_first, end=True)
    client.post("/api/command", json={"text": "好,先這樣收回,順便問一下沙盒最少要放行哪些網站", "thread": "talk"})
    assert given_back == [0]  # Muninn was still at its part
    assert runner.calls[-1] == ("send", TOLD) and h.engine.leases.return_idle() == 0  # told: it answers that first
    h.engine.leases.on_event(ev(lid, 3, "turn_end"))
    assert h.engine.leases.return_idle() == 1 and ("detach", "user") in runner.calls
