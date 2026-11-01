"""Lending a conversation to a project's session: the lease's life, the
session's words kept out of Muninn's understanding, giving it back once it goes
quiet or the user has moved on, and the permission requests a lent session
puts to the user."""

from __future__ import annotations

from datetime import timedelta

import pytest

from muninn.adapters.store_sqlite import SqliteStore
from muninn.core.lease import ChannelLease, PermissionDesk, answer_text, in_domains, suspicious_url
from muninn.core.ports import RunnerError

from conftest import ZH, FakeChannel, FakeClock, at


class FakeRunner:
    def __init__(self):
        self.calls = []
        self.fail: RunnerError | None = None

    def _rec(self, *call):
        if self.fail:
            raise self.fail
        self.calls.append(call)

    def start(self, project, session_id, thread, *, resume=False, mode="discuss"):
        self._rec("start", project, thread, resume)
        self.started_mode = mode
        return {"lease_id": session_id, "project": project, "mode": mode, "state": "attaching"}

    def send(self, lease_id, text, client_msg_id, attachments=()):
        self._rec("send", text)
        self.sent_files = [a.name for a in attachments]
        self.msg_ids = [*getattr(self, "msg_ids", []), client_msg_id]

    def events(self, lease_id, after_seq):
        return iter(())

    def file(self, lease_id, file_id):
        self._rec("file", lease_id, file_id)
        got = getattr(self, "files", {}).get((lease_id, file_id))
        if got is None:
            raise RunnerError("not_found")
        return got

    def keepalive(self, lease_id):
        self._rec("keepalive", lease_id)

    def terminal(self, lease_id):
        self._rec("terminal", lease_id)
        return {"command": f"resume-session {lease_id}", "hint": "在專案那台電腦的終端機貼上"}

    def detach(self, lease_id, reason):
        self._rec("detach", reason)

    def set_mode(self, lease_id, mode):
        self._rec("mode", mode)
        return {"lease_id": lease_id, "mode": mode}

    def policy(self):
        self.policy_reads = getattr(self, "policy_reads", 0) + 1
        return {"v": 1, "attachable": ["garden", "blog", *getattr(self, "sandboxes", [])],
                "work_projects": list(getattr(self, "sandboxes", [])),
                "no_session_domains": ["paste.example", "hooks.example"]}


class Threads:
    def __init__(self):
        self.d = {}

    def load(self, tid):
        return self.d.get(tid)

    def save(self, tid, t):
        self.d[tid] = t

    def update(self, tid, change):
        t = change(self.d.get(tid))
        if t is not None:
            self.d[tid] = t
        return t


@pytest.fixture
def setup():
    store, clock, channel, runner, threads = SqliteStore(":memory:"), FakeClock(at(2026, 10, 22, 15)), FakeChannel(), FakeRunner(), Threads()
    quiet = {"on": False}
    leases = ChannelLease(store, clock, runner, channel, lambda now: quiet["on"], threads.load, threads.update, words=ZH)
    desk = PermissionDesk(store, clock, channel, lambda now: quiet["on"], leases, words=ZH)
    return store, clock, channel, runner, threads, quiet, leases, desk


def ev(lease_id, seq, type_, **data):
    return {"v": 1, "seq": seq, "lease_id": lease_id, "ts": "", "type": type_, **data}


def test_attach_only_registered_projects_and_the_thread_points_at_its_lease(setup):
    store, _, _, runner, threads, _, leases, _ = setup
    with pytest.raises(ValueError, match="不能借用"):
        leases.attach(None, "secret-work")
    got = leases.attach(None, "garden")
    assert threads.d[got["thread"]]["lease"] == got["lease_id"]
    assert leases.lease_for(got["thread"])["project"] == "garden"
    with pytest.raises(ValueError, match="已經借出去"):
        leases.attach(got["thread"], "blog")


def test_high_usage_asks_before_attaching(setup):
    store, clock, *_ , leases, _ = setup
    store.set_state("lease:rate_limit", {"five_hour": {"utilization": 0.86, "resetsAt": clock.now().timestamp() + 3600}})
    first = leases.attach(None, "garden")
    assert "86%" in first["needs_confirm"] and "lease_id" not in first
    assert "lease_id" in leases.attach(None, "garden", confirm_quota=True)


def test_stale_usage_readings_are_ignored(setup):
    store, clock, *_, leases, _ = setup
    store.set_state("lease:rate_limit", {"five_hour": {"utilization": 0.9, "resetsAt": clock.now().timestamp() - 1},
                                         "seven_day": {"utilization": 0.95, "resetsAt": None, "at": clock.now().timestamp() - 7200}})
    assert leases.quota() == {} and leases.quota_warning() is None


def test_the_sessions_words_stay_out_of_the_thread_and_only_a_local_summary_comes_back(setup):
    store, clock, _, runner, threads, _, leases, _ = setup
    got = leases.attach(None, "garden")
    tid, lid = got["thread"], got["lease_id"]
    leases.send(tid, "銀行那件怎麼了?")
    assert ("send", "銀行那件怎麼了?") in runner.calls
    assert leases.on_event(ev(lid, 1, "assistant", text="repo 裡寫著帳號 123"))
    assert not leases.on_event(ev(lid, 1, "assistant", text="repo 裡寫著帳號 123"))  # a replayed event
    assert [t["role"] for t in store.lease_turns(lid)] == ["user", "session"]
    assert threads.d[tid]["turns"] == []  # nothing the session said is in the thread Muninn understands
    leases.on_event(ev(lid, 2, "handover", handover={"summary": "談了貸款文件", "decisions": [{"what": "先問理專"}],
                                                       "open_questions": [], "tasks_for_owner": [{"text": "打給理專"}]}))
    leases.on_event(ev(lid, 3, "state", state="detached"))
    t = threads.d[tid]
    assert "lease" not in t
    [back] = t["turns"]
    assert back["local_only"] and "談了貸款文件" in back["text"] and "打給理專" in back["text"]
    assert "123" not in back["text"]
    assert leases.lease_for(tid) is None


def test_a_terminal_lease_takes_no_messages_until_it_is_taken_back(setup):
    *_, runner, threads, _, leases, _ = setup
    got = leases.attach(None, "garden")
    assert leases.terminal(got["thread"])["command"] == f"resume-session {got['lease_id']}"
    with pytest.raises(ValueError, match="終端機"):
        leases.send(got["thread"], "hi")
    assert leases.detach(got["thread"])["state"] == "detaching"


def test_the_runner_refusing_is_said_plainly(setup):
    *_, runner, _, _, leases, _ = setup
    runner.fail = RunnerError("cap_reached")
    with pytest.raises(ValueError, match="上限"):
        leases.attach(None, "garden")


def test_a_runtime_that_cannot_hold_a_talk_says_what_it_lacks(setup):
    *_, runner, _, _, leases, _ = setup
    runner.fail = RunnerError("runtime_unsupported", "執行環境 plain 沒有長駐串流,不能開租約")
    with pytest.raises(ValueError, match="開不了深談.*沒有長駐串流"):
        leases.attach(None, "garden")


def test_an_idle_lease_is_given_back_without_asking(setup):
    store, _, channel, runner, threads, _, leases, _ = setup
    got = leases.attach(None, "garden")
    leases.on_event(ev(got["lease_id"], 1, "idle_warning", idle_s=1800))
    assert leases.view(got["thread"])["warn"] is True
    assert leases.return_idle() == 1 and ("detach", "idle") in runner.calls
    assert store.lease(got["lease_id"])["state"] == "detaching" and channel.sent == []
    assert leases.return_idle() == 0  # once
    leases.on_event(ev(got["lease_id"], 2, "handover", handover={"summary": "談了花園的排水"}))
    leases.on_event(ev(got["lease_id"], 3, "state", state="detached"))
    t = threads.d[got["thread"]]
    assert "lease" not in t and "排水" in t["turns"][-1]["text"]


def test_a_talk_the_user_left_for_another_conversation_is_given_back_at_once(setup):
    store, clock, _, runner, _, _, leases, _ = setup
    got = leases.attach(None, "garden")
    leases.send(got["thread"], "排水的坡度要多少?")
    leases.on_event(ev(got["lease_id"], 1, "turn_end"))
    clock.t += timedelta(hours=2)
    assert leases.return_idle() == 0  # quiet, but nothing said anywhere else: still theirs
    leases.left("elsewhere")  # then they talk about something else
    assert leases.return_idle() == 1 and ("detach", "user") in runner.calls  # the runner's reasons have no moved_on yet
    assert store.lease(got["lease_id"])["end_reason"] == "moved_on"


def test_a_session_still_at_it_finishes_before_it_is_given_back(setup):
    store, clock, _, runner, _, _, leases, _ = setup
    runner.sandboxes = ["idea-3"]
    got = leases.attach(None, "idea-3")
    assert got["mode"] == "work"
    leases.send(got["thread"], "用這三張照片重建客廳")
    clock.t += timedelta(minutes=1)
    leases.left("elsewhere")  # the experiment runs while he asks about the dentist
    leases.on_event(ev(got["lease_id"], 1, "tool_use", name="Bash"))
    clock.t += timedelta(hours=1)
    assert leases.return_idle() == 0  # not cut off in the middle
    leases.on_event(ev(got["lease_id"], 2, "turn_end"))
    assert leases.return_idle() == 1 and ("detach", "user") in runner.calls


def test_a_conversation_that_came_back_is_told_with_its_report_once_however_often_its_end_is_seen(setup):
    store, clock, channel, runner, threads, _, _, _ = setup
    back = []
    leases = ChannelLease(store, clock, runner, channel, lambda now: False, threads.load, threads.update,
                          ended=lambda lease, report: back.append((lease["project"], report)), words=ZH)
    got = leases.attach(None, "garden")
    leases.on_event(ev(got["lease_id"], 1, "handover", handover={"summary": "排水做完一半"}))
    leases.on_event(ev(got["lease_id"], 2, "state", state="detached"))
    leases._finish(store.lease(got["lease_id"]), clock.now(), lost=True)  # seen again, another way: nothing more
    assert back == [("garden", threads.d[got["thread"]]["turns"][-1]["text"])] and "排水做完一半" in back[0][1]
    assert sum(1 for x in threads.d[got["thread"]]["turns"] if x.get("ended")) == 1
    lost = leases.attach(None, "blog")
    runner.fail = RunnerError("not_found")
    leases.forget(lost["lease_id"])
    assert back[-1] == ("blog", None)  # broke off: no report


def test_a_sandbox_working_on_its_opening_is_not_cut_off(setup):
    store, clock, _, runner, threads, _, leases, _ = setup
    runner.sandboxes = ["idea-3"]
    tid = "t-idea"
    threads.d[tid] = said(("user", "用照片重建客廳試試"), ("muninn", "我開一個沙盒試。"))
    got = leases.lend(tid, "idea-3")
    assert got["opened"]
    clock.t += timedelta(seconds=20)
    leases.left("elsewhere")  # he asks about the dentist at once
    assert leases.return_idle() == 0
    leases.on_event(ev(got["lease_id"], 1, "error", error="process_crashed"))
    assert leases.return_idle() == 1  # a turn that ended in an error is over too


def test_coming_back_to_a_talk_keeps_it(setup):
    store, clock, _, runner, _, _, leases, _ = setup
    got = leases.attach(None, "garden")
    leases.left("elsewhere")
    clock.t += timedelta(minutes=1)
    leases.send(got["thread"], "還有一件事")  # said here after the word elsewhere
    leases.on_event(ev(got["lease_id"], 1, "turn_end"))
    assert leases.return_idle() == 0


def test_usage_numbers_from_the_runner_are_kept(setup):
    store, *_, leases, _ = setup
    lid = leases.attach(None, "garden")["lease_id"]
    leases.on_event(ev(lid, 1, "quota", status="warning", windows=[{"id": "short", "label": "5 小時", "used": 0.83, "resets_at": None},
                                                                     {"id": "week", "label": "7 天", "used": 0.4, "resets_at": None}]))
    assert leases.quota() == {"short": 0.83, "week": 0.4}
    assert "83%" in leases.quota_warning()


@pytest.mark.parametrize("url,flag", [
    ("https://arxiv.org/abs/2501.01234", False),
    ("https://docs.python.org/3/library/re.html?highlight=search", False),
    ("https://evil.example/c?d=" + "QmFzZTY0IGVuY29kZWQgc2VjcmV0IGRhdGEgZnJvbSB0aGUgcmVwbw", True),
    ("https://x.example/?" + "&".join(f"k{i}=v{i}" for i in range(20)), True),
    ("https://x.example/a/" + "Zm9vYmFyMTIzNDU2Nzg5MGFiY2RlZmdoaWprbG1u", True),
    # ordinary research links stay quiet
    ("https://github.com/anthropics/claude-code/blob/main/README.md", False),
    ("https://github.com/org/repo/blob/3f2a9c1e4b5d6a7f8e9d0c1b2a3f4e5d6c7b8a9f/src/module/file.py", False),
    ("https://docs.example.org/en/stable/reference/configuration/how-to-configure-the-long-option-names.html", False),
    ("https://arxiv.org/pdf/2501.01234v2", False),
    ("https://example.org/items/550e8400-e29b-41d4-a716-446655440000", False),
])
def test_urls_that_carry_data_are_flagged(url, flag):
    assert suspicious_url(url) is flag


def test_domain_suffixes():
    assert in_domains("paste.example", ["paste.example"]) and in_domains("a.paste.example", ["paste.example"])
    assert not in_domains("notpaste.example", ["paste.example"])


def test_a_question_waits_once_and_its_answer_goes_back(setup):
    store, clock, channel, _, _, _, leases, desk = setup
    lid = leases.attach(None, "garden")["lease_id"]
    req = {"v": 1, "id": f"{lid}:tu1", "lease": lid, "project": "garden", "kind": "ask_user",
           "input_summary": "A or B", "risk": "question", "question": {"questions": [{"question": "A or B?"}]}}
    desk.receive(req)
    desk.receive(req)  # the relay retried
    assert len(channel.sent) == 1 and "garden 問你" in channel.sent[0][0].title
    assert desk.result(req["id"]) is None
    with pytest.raises(ValueError, match="選一個"):
        desk.decide(req["id"], "allow")
    desk.decide(req["id"], "allow", {"A or B?": "B"})
    assert desk.result(req["id"]) == {"v": 1, "decision": "allow", "answers": {"A or B?": "B"}}


def test_quiet_hours_hold_a_request_unless_the_user_is_talking_to_that_session(setup):
    store, clock, channel, _, threads, quiet, leases, desk = setup
    got = leases.attach(None, "garden")
    lid = got["lease_id"]
    quiet["on"] = True
    clock.t += timedelta(minutes=30)  # the last message was half an hour ago
    desk.receive({"id": f"{lid}:a", "lease": lid, "project": "garden", "kind": "open_web", "input_summary": "https://a.example/"})
    assert channel.sent == []
    leases.send(got["thread"], "查一下")  # talking now
    desk.receive({"id": f"{lid}:b", "lease": lid, "project": "garden", "kind": "open_web", "input_summary": "https://b.example/"})
    assert len(channel.sent) == 1


def test_whole_conversation_allow_is_never_offered_for_sites_anyone_can_write_to(setup):
    *_, leases, desk = setup
    lid = leases.attach(None, "garden")["lease_id"]
    desk.receive({"id": "x1", "lease": lid, "project": "garden", "kind": "open_web", "input_summary": "https://a.paste.example/raw/1"})
    desk.receive({"id": "x2", "lease": lid, "project": "garden", "kind": "open_web", "input_summary": "https://arxiv.org/abs/1"})
    cards = {c["id"]: c for c in desk.pending()}
    assert cards["x1"]["allow_session"] is False and cards["x2"]["allow_session"] is True
    with pytest.raises(ValueError, match="任何人都能寫入"):
        desk.decide("x1", "allow_session")
    assert desk.decide("x2", "allow_session")["decision"] == "allow_session"


def test_unanswered_requests_are_denied_after_five_minutes(setup):
    store, clock, *_, desk = setup
    desk.receive({"id": "r1", "kind": "open_web", "input_summary": "https://a.example/"})
    clock.t += timedelta(minutes=5)
    assert desk.result("r1") == {"v": 1, "decision": "deny"}
    assert store.permission_request("r1")["by"] == "timeout"


def test_what_the_user_decided_stays_in_the_lent_conversation(setup):
    store, clock, *_, leases, desk = setup
    lid = leases.attach(None, "garden")["lease_id"]
    desk.receive({"id": "q", "lease": lid, "project": "garden", "kind": "ask_user", "input_summary": "A or B",
                  "question": {"questions": [{"question": "A or B?"}]}})
    desk.receive({"id": "w", "lease": lid, "project": "garden", "kind": "open_web", "input_summary": "https://arxiv.org/abs/1"})
    desk.receive({"id": "t", "lease": lid, "project": "garden", "kind": "open_web", "input_summary": "https://a.example/x"})
    assert desk.card("q")["lease"] == lid
    assert desk.decide("q", "allow", {"A or B?": "B"})["note"] == "你選了:B"
    assert desk.decide("w", "deny")["note"] == "拒絕打開 arxiv.org"
    assert desk.card("w") is None  # decided: no card any more
    clock.t += timedelta(minutes=5)
    desk.expire()
    assert [(t["role"], t["text"]) for t in store.lease_turns(lid)] == [
        ("answer", "你選了:B"), ("answer", "拒絕打開 arxiv.org"), ("answer", "沒回應,沒讓它打開 a.example")]


@pytest.mark.parametrize("decision,answers,text", [
    ("allow", {"Q1?": "甲", "Q2?": "乙"}, "你選了:Q1? → 甲;Q2? → 乙"),
    ("deny", None, "沒用選項回答,讓它用文字問"),
])
def test_answer_lines_for_questions(decision, answers, text):
    assert answer_text({"kind": "ask_user", "decision": decision, "answers": answers}, words=ZH) == text


def test_the_home_page_finds_every_lent_conversation_and_a_finished_one_can_be_reread(setup):
    store, *_, leases, desk = setup
    got = leases.attach(None, "garden")
    lid = got["lease_id"]
    assert leases.active() == [{"lease_id": lid, "project": "garden", "label": "garden", "thread": got["thread"], "state": "attaching", "warn": False}]
    leases.send(got["thread"], "這要怎麼做?")
    leases.on_event(ev(lid, 1, "handover", handover={"v": 1, "summary": "談完了", "open_questions": ["要不要加範圍?"]}))
    leases.on_event(ev(lid, 2, "state", state="detached"))
    assert leases.active() == []
    past = leases.transcript(lid)
    assert past["handover"]["summary"] == "談完了" and [t["role"] for t in past["turns"]] == ["user"]
    assert leases.thread_of(lid) == got["thread"]


def test_a_lease_the_runner_forgot_gives_the_thread_back(setup):
    store, _, _, runner, threads, _, leases, _ = setup
    got = leases.attach(None, "garden")
    runner.fail = RunnerError("not_found")
    assert leases.detach(got["thread"])["state"] == "detached"  # not stuck: over here too
    t = threads.d[got["thread"]]
    assert "lease" not in t and t["turns"][-1]["local_only"] and "斷掉" in t["turns"][-1]["text"]
    runner.fail = None
    other = leases.attach(None, "blog")
    runner.fail = RunnerError("not_found")
    with pytest.raises(ValueError, match="回到 Muninn"):
        leases.send(other["thread"], "還在嗎?")
    assert leases.lease_for(other["thread"]) is None


def test_lease_pushes_follow_the_counts_only_setting(setup):
    store, clock, channel, runner, threads, quiet, _, _ = setup
    leases = ChannelLease(store, clock, runner, channel, lambda now: False, threads.load, threads.update, push_titles=False, words=ZH)
    desk = PermissionDesk(store, clock, channel, lambda now: False, leases, push_titles=False, words=ZH)
    lid = leases.attach(None, "garden")["lease_id"]
    desk.receive({"id": "p1", "lease": lid, "project": "garden", "kind": "open_web", "input_summary": "https://a.example/"})
    assert [m.title for batch in channel.sent for m in batch] == [None]


def test_thread_writes_go_through_the_serializer(setup):
    *_, leases, _ = setup
    ran = []
    leases.serialize_with(lambda fn: (ran.append(1), fn())[1])
    got = leases.attach(None, "garden")
    leases.on_event(ev(got["lease_id"], 1, "state", state="detached"))
    assert len(ran) == 4  # pointing the thread at the lease; and, giving it back: its end claimed once, the thread, what follows


def test_a_sandbox_opened_a_moment_ago_is_lent_able_to_act_and_can_be_taken_back_to_read_only(setup):
    store, _, _, runner, threads, _, leases, _ = setup
    assert leases.attachable() == ["garden", "blog"]  # read before the sandbox was opened, and kept a while
    runner.sandboxes = ["idea-4"]
    got = leases.attach(None, "idea-4")
    assert got["mode"] == "work" and got["can_work"] is True and runner.started_mode == "work"
    assert not any(c[0] == "mode" for c in runner.calls)  # started that way: no switch, no restart
    view = leases.view(got["thread"])
    assert view["mode"] == "work" and view["can_work"]
    assert leases.set_mode(got["thread"], "discuss")["note"] == "改回唯讀"
    switched = leases.set_mode(got["thread"], "work")
    assert ("mode", "work") in runner.calls and switched["mode"] == "work"
    assert store.lease_turns(got["lease_id"])[-1]["text"].startswith("允許動手")


def test_a_project_that_may_not_act_starts_read_only(setup):
    *_, runner, _, _, leases, _ = setup
    got = leases.attach(None, "garden")
    assert got["mode"] == "discuss" and runner.started_mode == "discuss" and not got["opened"]


def said(*turns):
    return {"about": {"kind": "idea", "id": 4}, "turns": [
        {"role": r, "text": x, "ts": "", **({"cloud_ok": True} if r == "user" else {})} for r, x in turns]}


def test_a_sandbox_starts_from_what_was_said_before_it_was_lent(setup):
    store, _, _, runner, threads, _, leases, _ = setup
    runner.sandboxes = ["idea-4"]
    threads.d["t1"] = said(("user", "可行嗎?"), ("muninn", "粗略版可行,要給它幾個尺寸"), ("user", "(按了「開專案實際試試」)"))
    got = leases.attach("t1", "idea-4")
    [sent] = [c[1] for c in runner.calls if c[0] == "send"]
    assert "他:可行嗎?\nMuninn:粗略版可行,要給它幾個尺寸" in sent and got["opened"]
    assert store.lease_turns(got["lease_id"])[-1]["role"] == "answer"  # said so, never as the user's own words
    assert not any(x["role"] == "user" for x in store.lease_turns(got["lease_id"]))


def test_what_must_stay_here_is_never_handed_to_the_sandbox(setup):
    *_, runner, threads, _, leases, _ = setup
    runner.sandboxes = ["idea-4", "idea-5"]
    threads.d["kept"] = said(("user", "可行嗎?"), ("muninn", "可以"))
    threads.d["kept"]["turns"][1]["local_only"] = True  # a round kept local
    threads.d["uncleared"] = said(("user", "可行嗎?"), ("muninn", "可以"))
    threads.d["uncleared"]["turns"][0]["cloud_ok"] = False
    assert leases.attach("kept", "idea-4")["opened"]  # what went into it, and nothing of the round kept local
    assert [c[1] for c in runner.calls if c[0] == "send"] == ["可行嗎?"]
    assert not leases.attach("uncleared", "idea-5")["opened"]
    assert len([c for c in runner.calls if c[0] == "send"]) == 1  # what was not cleared never goes


def test_only_the_ideas_own_conversation_is_handed_over_and_an_old_local_round_does_not_hold_it_back(setup):
    *_, runner, threads, _, leases, _ = setup
    runner.sandboxes = ["idea-4", "idea-5"]
    threads.d["other"] = said(("user", "牙醫幾點?"), ("muninn", "三點"))
    threads.d["other"]["about"] = {"kind": "task", "id": "dentist"}
    assert leases.attach("other", "idea-4")["opened"]  # from a conversation about something else: only what went into it
    assert [c[1] for c in runner.calls if c[0] == "send"] == ["牙醫幾點?"]
    threads.d["long"] = said(("user", "可行嗎?"), ("muninn", "交接摘要"), *[("user", "再說說"), ("muninn", "好")] * 6)
    threads.d["long"]["turns"][1]["local_only"] = True  # long before what is handed over
    assert leases.attach("long", "idea-5")["opened"]


def test_when_everything_goes_to_the_cloud_only_a_round_kept_local_stays_here(setup):
    store, clock, channel, runner, threads, quiet, _, _ = setup
    leases = ChannelLease(store, clock, runner, channel, lambda now: False, threads.load, threads.update, share_uncleared=True, words=ZH)
    runner.sandboxes = ["idea-4", "idea-5"]
    threads.d["uncleared"] = said(("user", "可行嗎?"), ("muninn", "可以"))
    threads.d["uncleared"]["turns"][0]["cloud_ok"] = False
    threads.d["kept"] = said(("user", "可行嗎?"), ("muninn", "可以"))
    threads.d["kept"]["turns"][1]["local_only"] = True
    assert leases.attach("uncleared", "idea-4")["opened"]
    assert leases.attach("kept", "idea-5")["opened"]
    assert [c[1] for c in runner.calls if c[0] == "send"][-1] == "可行嗎?"  # what went into it; the round kept local stays


def test_trying_an_idea_lends_the_conversation_and_says_so(setup):
    *_, runner, threads, _, leases, _ = setup
    runner.sandboxes = ["idea-4"]
    got = leases.lend("t2", "idea-4")
    assert leases.lease_for("t2")["project"] == "idea-4" and "開了 idea-4 在試:接下來你說的會直接給它" in got["text"]
    store_quota = setup[0]
    store_quota.set_state("lease:rate_limit", {"five_hour": {"utilization": 0.9, "resetsAt": setup[1].now().timestamp() + 3600}})
    asked = leases.lend("t3", "idea-4")
    assert "needs_confirm" in asked and "text" not in asked and leases.lease_for("t3") is None


def test_only_a_project_the_runner_lets_work_may_act_and_a_busy_session_says_so(setup):
    *_, runner, _, _, leases, _ = setup
    garden = leases.attach(None, "garden")
    with pytest.raises(ValueError, match="專案登錄沒開,或沙盒還沒準備好"):
        leases.set_mode(garden["thread"], "work")
    assert not any(c[0] == "mode" for c in runner.calls)
    runner.sandboxes = ["idea-4"]
    idea = leases.attach(None, "idea-4")
    runner.fail = RunnerError("busy")
    with pytest.raises(ValueError, match="這輪結束再切換"):
        leases.set_mode(idea["thread"], "discuss")
    runner.fail = None
    assert leases.view(idea["thread"])["mode"] == "work"


def test_viewing_a_lent_project_does_not_ask_the_runner_each_time(setup):
    *_, runner, _, _, leases, _ = setup
    got = leases.attach(None, "garden")
    reads = runner.policy_reads
    for _ in range(3):
        assert leases.view(got["thread"])["can_work"] is False
    assert runner.policy_reads == reads


def test_writing_a_file_can_be_allowed_for_the_whole_talk_but_a_command_only_once(setup):
    store, _, channel, runner, _, _, leases, desk = setup
    runner.sandboxes = ["idea-4"]
    lid = leases.attach(None, "idea-4")["lease_id"]
    desk.receive({"id": "f", "lease": lid, "project": "idea-4", "kind": "write_file", "input_summary": "notes/plan.md"})
    desk.receive({"id": "c", "lease": lid, "project": "idea-4", "kind": "run_command", "input_summary": "python try.py"})
    assert [m[0].title for m in channel.sent] == ["idea-4 想改一個檔案", "idea-4 想跑一個指令"]
    cards = {c["id"]: c for c in desk.pending()}
    assert (cards["f"]["kind"], cards["f"]["summary"], cards["f"]["allow_session"]) == ("write_file", "notes/plan.md", True)
    assert (cards["c"]["kind"], cards["c"]["allow_session"]) == ("run_command", False)
    with pytest.raises(ValueError, match="每一個都要問"):
        desk.decide("c", "allow_session")
    assert desk.decide("f", "allow_session")["note"] == "這次深談都允許改這個專案資料夾裡的檔案"
    assert desk.decide("c", "allow")["note"] == "允許跑:python try.py"
    assert desk.result("c") == {"v": 1, "decision": "allow"}


def test_coming_back_to_a_sandbox_resumes_its_session_and_tells_it_only_what_came_since(setup):
    store, clock, _, runner, threads, _, leases, _ = setup
    runner.sandboxes = ["idea-4"]
    first = leases.attach("t9", "idea-4")
    leases.on_event(ev(first["lease_id"], 1, "handover", handover={"summary": "試了三張照片"}))
    leases.on_event(ev(first["lease_id"], 2, "state", state="detached"))
    t = threads.d["t9"]
    t["about"] = {"kind": "idea", "id": 4}
    t["turns"] = [{"role": "user", "text": "去試試", "ts": "", "cloud_ok": True}, *t["turns"]]
    assert t["turns"][-1]["local_only"]  # the handover line
    clock.t += timedelta(minutes=3)
    t["turns"].append({"role": "user", "text": "我量了,長牆 4.05 m", "ts": clock.now().isoformat(), "cloud_ok": True})
    again = leases.attach("t9", "idea-4")
    assert again["lease_id"] == first["lease_id"] and runner.calls[-2][:4] == ("start", "idea-4", "t9", True)
    sent = [c[1] for c in runner.calls if c[0] == "send"][-1]
    assert again["opened"] and "4.05 m" in sent and "試了三張照片" not in sent and "去試試" not in sent
    back = store.lease(first["lease_id"])
    assert back["ended"] is None and back["handover"] is None and leases.lease_for("t9")["lease_id"] == first["lease_id"]
    garden = leases.attach(None, "garden")  # another project's handover in an idea's conversation
    leases.on_event(ev(garden["lease_id"], 1, "state", state="detached"))
    threads.d["t10"] = {"about": {"kind": "idea", "id": 4}, "turns": [
        {"role": "user", "text": "去試試", "ts": "", "cloud_ok": True},
        {"role": "muninn", "text": "garden 的交接", "ts": "", "local_only": True, "via": f"lease:{garden['lease_id']}"}]}
    assert leases.attach("t10", "idea-4")["opened"]
    assert [c[1] for c in runner.calls if c[0] == "send"][-1] == "去試試"  # not the other project's handover


def _ended(store, clock, leases, threads):
    """A talk with garden that has ended, its session having heard one line of it."""
    got = leases.attach(None, "garden")
    leases.send(got["thread"], "排水要怎麼做?")
    leases.on_event(ev(got["lease_id"], 1, "assistant", text="先看坡度"))
    leases.on_event(ev(got["lease_id"], 2, "handover", handover={"summary": "排水談到坡度"}))
    leases.on_event(ev(got["lease_id"], 3, "state", state="detached"))
    return got


def said_to_muninn(threads, tid, clock, text):
    clock.t += timedelta(minutes=1)
    threads.d[tid]["turns"].append({"role": "user", "text": text, "ts": clock.now().isoformat(), "cloud_ok": True})


def test_going_into_a_talk_again_resumes_it_hearing_what_was_said_since_and_says_so_in_the_conversation(setup):
    store, clock, _, runner, threads, _, leases, _ = setup
    got = _ended(store, clock, leases, threads)
    said_to_muninn(threads, got["thread"], clock, "量好了,坡度 2%")
    back = leases.lend(got["thread"], "garden")
    assert back["lease_id"] == got["lease_id"] and back["resumed"] and runner.calls[-2][:4] == ("start", "garden", got["thread"], True)
    sent = [c[1] for c in runner.calls if c[0] == "send"][-1]
    assert "坡度 2%" in sent and "排水談到坡度" not in sent  # what it does not know, not its own handover back
    entry = threads.d[got["thread"]]["turns"][-1]
    assert entry["note"] and entry["entered"] == got["lease_id"] and "garden" in entry["text"]  # kept: where it began


def test_going_into_a_talk_writing_its_handover_waits_then_goes_on_with_the_files(setup):
    from muninn.core.models import Attachment

    store, clock, _, runner, threads, _, leases, _ = setup
    photo = Attachment("slope.jpg", "image/jpeg", b"jpg", "a1b2c3d4e5f60718")
    leases.files_from(lambda ids: [photo] if list(ids) == [photo.id] else [])
    got = leases.attach(None, "garden")
    leases.on_event(ev(got["lease_id"], 1, "turn_end"))
    clock.t += timedelta(minutes=1)
    leases.left("elsewhere")
    assert leases.return_idle() == 1  # writing its handover
    said_to_muninn(threads, got["thread"], clock, "這是坡度的照片")
    sent_with = []
    runner.send = lambda lease_id, text, cid, attachments=(): sent_with.append((text, [a.name for a in attachments]))
    waiting = leases.lend(got["thread"], "garden", [photo])
    assert waiting["state"] == "detaching" and "寫完上一段的交接" in waiting["text"]
    assert threads.d[got["thread"]]["turns"][-1]["entered"] == got["lease_id"]
    assert leases.sweep_waiting() == [] and sent_with == []  # not before it has written it
    leases.on_event(ev(got["lease_id"], 2, "state", state="detached"))
    assert leases.sweep_waiting() == []
    assert len(sent_with) == 1 and "這是坡度的照片" in sent_with[0][0] and sent_with[0][1] == ["slope.jpg"]
    assert leases.sweep_waiting() == [] and len(sent_with) == 1  # once


def test_a_talk_that_cannot_be_gone_into_again_says_why(setup):
    store, clock, _, runner, threads, _, leases, _ = setup
    got = leases.attach(None, "garden")
    leases.on_event(ev(got["lease_id"], 1, "turn_end"))
    clock.t += timedelta(minutes=1)
    leases.left("elsewhere")
    leases.return_idle()
    leases.lend(got["thread"], "garden")
    clock.t += timedelta(minutes=5)
    assert leases.sweep_waiting() == [(got["lease_id"], "交接一直沒寫完")]
    assert "沒能接回 garden(交接一直沒寫完)" in threads.d[got["thread"]]["turns"][-1]["text"]
    assert store.get_state("lease:waiting") == {}  # said once, nothing left to drop silently


def test_a_talk_that_comes_back_keeps_what_its_handover_said_for_the_conversation_to_draw(setup):
    store, clock, _, runner, threads, _, leases, _ = setup
    got = leases.attach(None, "garden")
    threads.d[got["thread"]]["topic"] = {"title": "後院排水", "state": "waiting", "now": "等你量坡度", "ts": "2026-10-20T10:00:00+08:00"}
    leases.on_event(ev(got["lease_id"], 1, "handover", handover={
        "summary": "排水談到坡度", "decisions": [{"what": "坡度 2%", "why": "雨水才排得掉"}, {"why": "沒有內容的不算"}],
        "open_questions": ["要不要加集水井?", ""], "tasks_for_owner": [{"text": "量後院長度", "due": "2026-10-30"}]}))
    leases.on_event(ev(got["lease_id"], 2, "state", state="detached"))
    turn = threads.d[got["thread"]]["turns"][-1]
    assert turn["handover"] == {"project": "garden", "fallback": False, "summary": "排水談到坡度",
                                "decisions": [{"what": "坡度 2%", "why": "雨水才排得掉"}],
                                "open_questions": ["要不要加集水井?"], "tasks_for_owner": [{"text": "量後院長度", "due": "2026-10-30"}]}
    assert "坡度 2%" in turn["text"]  # the text beside it is still what a model reads
    topic = threads.d[got["thread"]]["topic"]  # no one is at it now: open, with where the talk got to
    assert (topic["title"], topic["state"]) == ("後院排水", "open") and topic["now"].startswith("跟 garden 談到 ")
    lost = leases.attach(None, "blog")
    runner.fail = RunnerError("not_found")
    leases.forget(lost["lease_id"])
    assert "handover" not in threads.d[lost["thread"]]["turns"][-1]  # lost, not handed over: no card


def test_quota_windows_reset_at_the_runners_iso_times_and_a_resume_that_failed_is_said(setup):
    store, clock, _, runner, _, _, leases, _ = setup
    lid = leases.attach(None, "garden")["lease_id"]
    later = (clock.now() + timedelta(hours=2)).isoformat()
    leases.on_event(ev(lid, 1, "quota", status="warning", windows=[{"id": "w5", "label": "5 小時", "used": 0.9, "resets_at": later}]))
    assert leases.quota() == {"w5": 0.9} and "5 小時 90%" in leases.quota_warning()
    clock.t += timedelta(hours=3)
    assert leases.quota() == {}  # past its reset
    leases.on_event(ev(lid, 2, "state", state="running", resumed=False, detail="上次的紀錄已經不在了"))
    assert "接不回上次那段對話(上次的紀錄已經不在了)" in store.lease_turns(lid)[-1]["text"]


def test_the_screens_hear_of_a_lease_when_what_they_show_of_it_changes_and_only_then(setup):
    store, clock, _, runner, _, _, leases, _ = setup
    told = []
    leases.on_change(lambda lease_id, lease: told.append((lease["project"], lease["state"], lease["mode"])))
    lid = leases.attach(None, "garden")["lease_id"]
    leases.on_event(ev(lid, 1, "text_delta", text="排水"))  # only its count of events moved
    assert told == []
    leases.on_event(ev(lid, 2, "state", state="running"))
    leases.renamed("garden", "garden-plan")
    assert told == [("garden", "running", "discuss"), ("garden-plan", "running", "discuss")]


def test_a_file_the_session_showed_is_kept_in_the_talk_and_read_by_name_only(setup):
    from muninn.core.lease import talk_lines

    store, _, _, runner, _, _, leases, _ = setup
    lid = leases.attach(None, "garden")["lease_id"]
    leases.on_event(ev(lid, 1, "file", id="0123456789abcdef", path="results/plan.png", name="plan.png", mime="image/png",
                       size=1234, summary="排水平面圖"))
    [turn] = [t for t in store.lease_turns(lid) if t["role"] == "file"]
    assert turn["text"] == "排水平面圖" and turn["files"] == [{"id": "0123456789abcdef", "name": "plan.png", "media_type": "image/png",
                                                            "size": 1234, "path": "results/plan.png", "lease": lid}]
    assert talk_lines(store.lease_turns(lid), "garden") == ["garden:(showed the user plan.png)"]  # named, never read


def test_a_talk_whose_project_the_runner_renamed_goes_on_under_the_new_name(setup):
    store, _, _, _, _, _, leases, _ = setup
    told = []
    leases.on_change(lambda lease_id, lease: told.append(lease["project"]))
    lid = leases.attach(None, "garden")["lease_id"]
    leases.on_event(ev(lid, 1, "state", state="running", project="garden-plan", mode="discuss"))
    assert store.lease(lid)["project"] == "garden-plan" and told == ["garden-plan"]


def test_what_a_talk_left_undecided_is_its_latest_handovers_and_goes_back_to_it_when_it_is_entered_again(setup):
    store, clock, _, runner, threads, _, leases, _ = setup
    got = leases.attach(None, "garden")
    leases.on_event(ev(got["lease_id"], 1, "handover", handover={"summary": "排水", "open_questions": ["坡度要 1% 還是 2%?", "要不要集水井?"]}))
    leases.on_event(ev(got["lease_id"], 2, "state", state="detached"))
    left = threads.d[got["thread"]]["talk_left"]
    assert (left["project"], left["questions"]) == ("garden", ["坡度要 1% 還是 2%?", "要不要集水井?"])
    said_to_muninn(threads, got["thread"], clock, "繼續排水")
    leases.lend(got["thread"], "garden")
    sent = [c[1] for c in runner.calls if c[0] == "send"][-1]
    assert "繼續排水" in sent and "坡度要 1% 還是 2%?" in sent  # its own, asked in the talk
    assert "talk_left" not in threads.d[got["thread"]]  # in the talk's hands now, waiting nowhere else
    leases.on_event(ev(got["lease_id"], 3, "handover", handover={"summary": "集水井不做", "open_questions": []}))
    leases.on_event(ev(got["lease_id"], 4, "state", state="detached"))
    assert "talk_left" not in threads.d[got["thread"]]  # the same talk again, nothing left open: nothing waits


# ── what Muninn said beside a talk ──────────────────────────────────────────

def test_lines_read_beside_a_talk_say_whose_they_are():
    from muninn.core.lease import talk_lines

    turns = [{"role": "user", "text": "沙盒要開哪些?"}, {"role": "session", "text": "我先不處理,交給 Muninn"},
             {"role": "muninn", "text": "先開 seeds.example。"}, {"role": "relay", "text": "沙盒先只放行 seeds.example。"},
             {"role": "answer", "text": "允許跑:ls"}]
    assert talk_lines(turns, "garden") == ["the user:沙盒要開哪些?", "garden:我先不處理,交給 Muninn", "Muninn:先開 seeds.example。",
                                           "Muninn told garden:沙盒先只放行 seeds.example。"]


def test_what_muninn_said_beside_a_talk_is_kept_in_its_conversation_apart_from_the_handover(setup):
    store, clock, _, runner, threads, _, leases, _ = setup
    leases._share_uncleared = True
    got = leases.attach(None, "garden")
    tid, lid = got["thread"], got["lease_id"]
    store.add_lease_turn(lid, "muninn", "上次說過的", clock.now() - timedelta(days=1))  # before this time in: not again
    leases.send(tid, "沙盒要開哪些?")
    leases.on_event(ev(lid, 1, "turn_end"))
    store.add_lease_turn(lid, "muninn", "先開 seeds.example。\n記到需求清單了", clock.now())
    assert leases.relay(lid, "沙盒先只放行 seeds.example。")
    assert not leases.relay(lid, "登入在他自己的電腦上做。")  # it is answering the first: waits
    leases.on_event(ev(lid, 2, "handover", handover={"summary": "談了沙盒", "open_questions": ["要不要加圖片的網站?"]}))
    leases.on_event(ev(lid, 3, "state", state="detached"))
    kept, back = threads.d[tid]["turns"]
    assert kept["beside"] == lid and kept["local_only"] and kept["via"] == [f"lease:{lid}"] and kept["role"] == "muninn"
    assert kept["text"] == ("跟 garden 談的時候,我在旁邊說了:\n- 先開 seeds.example。 記到需求清單了\n"
                            "- 告訴 garden:沙盒先只放行 seeds.example。\n- 還沒來得及告訴 garden:登入在他自己的電腦上做。")
    assert back["ended"] == lid and "seeds.example" not in back["text"]  # the handover's text is the talk's report alone
    assert not store.get_state("lease:relay")  # nothing left waiting for a talk that is over
    runner.calls.clear()
    leases.attach(tid, "garden")  # the same talk again: it is not told back what Muninn said beside it
    assert not any(c[0] == "send" and "我在旁邊說了" in c[1] for c in runner.calls)


def test_a_talk_being_given_back_or_in_a_terminal_is_not_told(setup):
    store, _, _, runner, threads, _, leases, _ = setup
    leases._share_uncleared = True
    got = leases.attach(None, "garden")
    leases.terminal(got["thread"])
    assert not leases.relay(got["lease_id"], "沙盒先只放行 seeds.example。")
    leases.detach(got["thread"])
    leases.on_event(ev(got["lease_id"], 1, "state", state="detached"))
    assert not any(c[0] == "send" for c in runner.calls)
    assert "還沒來得及告訴 garden:沙盒先只放行 seeds.example。" in threads.d[got["thread"]]["turns"][0]["text"]


def test_a_talk_left_with_something_untold_tries_telling_it_again_before_it_is_given_back(setup):
    store, clock, _, runner, _, _, leases, _ = setup
    leases._share_uncleared = True
    got = leases.attach(None, "garden")
    leases.send(got["thread"], "沙盒要開哪些?")
    leases.on_event(ev(got["lease_id"], 1, "turn_end"))
    runner.fail = RunnerError("unreachable")
    assert not leases.relay(got["lease_id"], "沙盒先只放行 seeds.example。")
    runner.fail = None
    clock.t += timedelta(minutes=1)
    leases.left("elsewhere")
    assert leases.return_idle() == 0 and runner.calls[-1][1].startswith("(Muninn 剛才在旁邊回他")  # told first
    leases.on_event(ev(got["lease_id"], 2, "turn_end"))
    assert leases.return_idle() == 1 and ("detach", "user") in runner.calls


def test_a_talk_the_user_ended_and_goes_into_again_is_not_given_back_when_its_session_finishes_answering(setup):
    """Seen live: a talk once ended with 「今天先這樣」 kept that wish, and every time it was gone into again it was
    given back as soon as its session finished its first answer."""
    store, clock, _, runner, threads, _, leases, _ = setup
    got = leases.attach(None, "garden")
    leases.end_when_done(got["thread"])  # the user said they are done for now
    leases.on_event(ev(got["lease_id"], 1, "turn_end"))
    assert leases.return_idle() == 1
    leases.on_event(ev(got["lease_id"], 2, "handover", handover={"summary": "排水談到坡度"}))
    leases.on_event(ev(got["lease_id"], 3, "state", state="detached"))
    said_to_muninn(threads, got["thread"], clock, "坡度量好了 2%")
    back = leases.lend(got["thread"], "garden")
    assert back["lease_id"] == got["lease_id"]
    leases.on_event(ev(got["lease_id"], 4, "state", state="ready"))
    leases.on_event(ev(got["lease_id"], 5, "turn_end"))  # its answer to what was said since
    clock.t += timedelta(seconds=30)
    assert leases.return_idle() == 0 and store.lease(got["lease_id"])["state"] not in ("detaching", "detached")


def test_every_column_of_a_lease_is_cleared_set_afresh_or_its_own_for_good_when_it_is_lent_again():
    from muninn.core.lease import STRETCH

    store = SqliteStore(":memory:")
    columns = {r["name"] for r in store._q("PRAGMA table_info(leases)")}
    afresh = {"thread", "project", "mode", "state", "started", "last_activity", "last_user_at"}  # attach's `fresh`
    for_good = {"lease_id", "last_seq"}  # the session's own: its id, and how far its events were read
    assert columns == set(STRETCH) | afresh | for_good, "a new column of a lease: say whether lending it again clears it"
