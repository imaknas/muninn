"""Which conversation a sentence belongs to: judged by the local model, only when there is a choice."""

from __future__ import annotations

import json

from conftest import at, make_harness
from test_lease_precheck import cloud_first


def thread(h, tid, said, ts, about=None):
    h.store.save_thread(tid, {"about": about, "ts": ts, "turns": [{"role": "user", "text": said, "ts": ts},
                                                                    {"role": "muninn", "text": "好", "ts": ts}]})


def answers(h, reply):
    """The local model's answer; the cloud would say something else."""
    h.local.reply = json.dumps(reply)
    h.cloud.reply = json.dumps({"topic": "current"})


def test_with_nothing_else_going_on_nothing_is_asked():
    h = make_harness(at(2026, 10, 25, 10))
    thread(h, "a", "今天先做什麼?", "2026-10-25T09:00")
    thread(h, "old", "上個月的事", "2026-10-01T09:00")  # long quiet: not going on any more
    calls = len(h.local.calls) + len(h.cloud.calls)
    c = h.engine.topics().choose("a", "那先投遞")
    assert (c.thread, c.switched) == ("a", False) and len(h.local.calls) + len(h.cloud.calls) == calls


def test_a_sentence_about_another_conversation_goes_there_asked_as_the_routing_says():
    h = make_harness(at(2026, 10, 25, 10))
    cloud_first(h)
    n = h.engine.ideas.add("用照片重建房間的 3D 模型")["n"]
    thread(h, "dentist", "明天幾點看牙醫?", "2026-10-25T09:00")
    thread(h, "room", "先看重建準不準", "2026-10-25T09:30", about={"kind": "idea", "id": n})
    t = h.store.thread("room")
    t["turns"].append({"role": "muninn", "text": "交接摘要:苗圃的報價在 repo 裡", "ts": "x", "local_only": True})
    h.store.save_thread("room", t)
    h.cloud.reply = json.dumps({"topic": "dentist", "why": "牙醫"})
    c = h.engine.topics().choose("room", "那牙醫要帶健保卡嗎?")
    assert (c.thread, c.switched, c.title) == ("dentist", True, "明天幾點看牙醫?")
    [(_, prompt)] = h.cloud.calls  # everything goes to the cloud, as the user set it
    assert "交接摘要" in prompt and h.local.calls == []  # a deep talk's handover too: only the model reads it
    [case] = h.store.eval_cases("turn_topic")
    assert case["verdict"]["label"] == "dentist"


def test_a_new_subject_starts_a_new_conversation_and_a_muddled_answer_stays_put():
    h = make_harness(at(2026, 10, 25, 10))
    thread(h, "a", "今天先做什麼?", "2026-10-25T09:00")
    thread(h, "b", "牙醫幾點?", "2026-10-25T09:10")
    answers(h, {"topic": "new"})
    assert h.engine.topics().choose("a", "我想換手機") == h.engine.topics().choose("a", "我想換手機")
    c = h.engine.topics().choose("a", "我想換手機")
    assert (c.thread, c.switched) == (None, True)
    answers(h, {"topic": "zzz"})
    assert h.engine.topics().choose("a", "嗯").thread == "a"


def test_what_the_user_opened_themselves_is_not_second_guessed():
    h = make_harness(at(2026, 10, 25, 10))
    thread(h, "a", "今天先做什麼?", "2026-10-25T09:00")
    thread(h, "b", "牙醫幾點?", "2026-10-25T09:10")
    calls = len(h.local.calls)
    assert h.engine.topics().choose("a", "這件呢", pinned=True).thread == "a" and len(h.local.calls) == calls


def say(h, text, reply, thread=None):
    h.cloud.reply = h.local.reply = json.dumps(reply, ensure_ascii=False)
    c = h.engine.conversation()
    turn = c.understand_turn(thread, text)
    c.execute_turn(turn)
    return turn["thread"]


def test_each_round_says_what_the_conversation_is_about_and_where_it_stands():
    h = make_harness(at(2026, 10, 25, 10))
    tid = say(h, "明天幾點看牙醫?", {"actions": [], "reply": "10:30,要提醒你嗎?",
                                     "topic": {"title": "牙醫", "state": "open", "now": "等你說要不要提醒"}, "question_for_him": "等你說要不要提醒"})
    [t] = h.engine.topics().today()
    assert (t["thread"], t["title"], t["state"], t["now"]) == (tid, "牙醫", "waiting", "等你說要不要提醒")
    say(h, "好", {"actions": [], "reply": "10:00 提醒你。", "topic": {"title": "牙醫", "state": "closed", "now": "10:00 提醒"}}, tid)
    [t] = h.engine.topics().today()
    assert t["state"] == "closed"  # closed today: still listed today
    h.clock.t = at(2026, 10, 26, 10)
    assert h.engine.topics().today() == []  # the next day it is gone from the list


def test_a_topic_is_closed_or_set_aside_by_saying_so_and_one_left_a_week_is_set_aside():
    h = make_harness(at(2026, 10, 25, 10))
    say(h, "想換手機", {"actions": [], "reply": "預算多少?", "topic": {"title": "換手機", "state": "open", "now": "等你說預算"}, "question_for_him": "等你說預算"})
    say(h, "牙醫幾點", {"actions": [], "reply": "10:30", "topic": {"title": "牙醫", "state": "open", "now": "要不要提醒"}, "question_for_him": "要不要提醒"})
    out = say(h, "換手機先不管", {"actions": [{"tool": "settle_topic", "args": {"topic": "換手機", "decision": "shelve"}}], "reply": "",
                                  "topic": {"title": "其他", "state": "closed", "now": ""}})
    states = {t["title"]: t["state"] for t in h.engine.topics().today()}
    assert states["換手機"] == "shelved" and states["牙醫"] == "waiting"  # set aside: what it asked waits no more
    assert out
    h.clock.t = at(2026, 11, 2, 11)
    assert h.engine.topics().shelve_quiet() == ["牙醫"]  # a week untouched
    assert [(t["title"], t["state"]) for t in h.engine.topics().today()] == [("牙醫", "shelved")]  # set aside today: shown today


def test_conversations_from_before_states_are_not_listed_as_waiting():
    h = make_harness(at(2026, 10, 25, 10))
    thread(h, "old", "今天先做什麼?", "2026-10-25T09:00")
    assert h.engine.topics().today() == []


def test_naming_an_idea_by_its_number_goes_to_its_conversation_without_asking():
    h = make_harness(at(2026, 10, 25, 10))
    n = h.engine.ideas.add("用照片重建房間的 3D 模型")["n"]
    thread(h, "room", "先看重建準不準", "2026-10-25T08:00", about={"kind": "idea", "id": n})
    thread(h, "packet", "這份投遞包可以送嗎", "2026-10-25T09:30", about={"kind": "packet", "id": 3})
    calls = len(h.local.calls) + len(h.cloud.calls)
    c = h.engine.topics().choose("packet", f"接續idea{n}的討論")
    assert (c.thread, c.switched, c.title) == ("room", True, "用照片重建房間的 3D 模型")
    assert len(h.local.calls) + len(h.cloud.calls) == calls  # named: no model asked
    m = h.engine.ideas.add("澆花提醒")["n"]
    c = h.engine.topics().choose("packet", f"想法 {m} 那個呢")
    assert (c.thread, c.switched, c.about) == (None, True, {"kind": "idea", "id": str(m)})  # no conversation yet: a new one about it


def test_the_model_is_told_what_each_conversation_is_called():
    h = make_harness(at(2026, 10, 25, 10))
    n = h.engine.ideas.add("用照片重建房間的 3D 模型")["n"]
    thread(h, "room", "先看重建準不準", "2026-10-25T08:00", about={"kind": "idea", "id": n})
    thread(h, "packet", "這份投遞包可以送嗎", "2026-10-25T09:30")
    answers(h, {"topic": "room"})
    h.engine.topics().choose("packet", "剛剛那個房間的呢")
    sent = json.loads(h.local.calls[-1][1])
    assert sent["others"][0]["refers_to"] == f"idea-{n}, idea {n}"
