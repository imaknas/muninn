"""Muninn speaking up when the user pauses: one thing, in one sentence, in its own conversation."""

from __future__ import annotations

import json
from datetime import timedelta

from conftest import at, make_harness

from muninn.core.actions import DecidedBy, TrackerStatus


def talked(h):
    h.cloud.reply = h.local.reply = json.dumps({"actions": [], "reply": "好"})
    c = h.engine.conversation()
    c.execute_turn(c.understand_turn(None, "今天先做投遞"))


def test_after_a_pause_one_thing_is_said_once_in_its_own_conversation_and_answered_with_its_buttons():
    h = make_harness(at(2026, 10, 25, 10))
    h.engine.gate.run(TrackerStatus("jobs", "acme", "interview", "Acme 寄來面試邀請"), DecidedBy.INFERRED_UNSURE)
    talked(h)
    assert h.engine.speak_up().run() is None  # still talking
    h.clock.t += timedelta(minutes=25)
    said = h.engine.speak_up().run()
    assert said and "Acme" in said
    assert h.engine.speak_up().run() is None  # once per pause
    assert [m[0].title for m in h.channel.sent].count("Muninn 想問你一件事") == 1
    [spoken] = [t for t in h.engine.conversation().feed() if any(x.get("spoken") for x in t["turns"])]
    assert spoken["about"]["kind"] == "question" and [s["label"] for s in spoken["suggest"]][:1] == ["要"]
    h.engine.conversation().act(spoken["thread"], 0)  # pressing the button that goes with the sentence answers it
    assert not [c for c in h.engine.pending_confirmations() if c.kind == "action"]


def test_quiet_hours_long_absence_and_nothing_waiting_say_nothing():
    h = make_harness(at(2026, 10, 25, 10))
    talked(h)
    h.clock.t += timedelta(minutes=25)
    assert h.engine.speak_up().run() is None  # nothing waits (the harness's plan items are confirmed)
    h2 = make_harness(at(2026, 10, 25, 10), confirm_all=False)
    talked(h2)
    h2.clock.t += timedelta(hours=4)
    assert h2.engine.speak_up().run() is None  # away, not a pause
    h3 = make_harness(at(2026, 10, 25, 23, 40), confirm_all=False)
    talked(h3)
    h3.clock.t += timedelta(minutes=25)
    assert h3.engine.speak_up().run() is None  # quiet hours


def test_the_evening_says_what_the_conversation_left_open():
    h = make_harness(at(2026, 10, 25, 21))
    h.cloud.reply = h.local.reply = json.dumps({"actions": [], "reply": "預算多少?",
                                                "topic": {"title": "換手機", "state": "open", "now": "等你說預算"}, "question_for_him": "換手機的預算是多少?"}, ensure_ascii=False)
    c = h.engine.conversation()
    c.execute_turn(c.understand_turn(None, "想換手機"))
    h.cloud.reply = h.local.reply = "還不錯的一天。"
    [summary] = h.engine.evening()
    assert "還在等你的:換手機的預算是多少?" in summary.body  # the question itself, readable on its own


def test_a_deep_talk_going_on_is_no_pause_and_a_session_at_work_is_never_interrupted():
    h = make_harness(at(2026, 10, 25, 10))
    h.engine.gate.run(TrackerStatus("jobs", "acme", "interview", "Acme 寄來面試邀請"), DecidedBy.INFERRED_UNSURE)
    talked(h)
    h.clock.t += timedelta(minutes=30)
    now = h.clock.now().isoformat()
    h.store.add_lease({"lease_id": "L1", "thread": "t-deep", "project": "idea-1", "mode": "work", "state": "running",
                       "started": now, "last_user_at": now, "last_activity": now})
    assert h.engine.speak_up().run() is None  # he is talking to the sandbox, not paused
    h.clock.t += timedelta(minutes=25)
    h.store.update_lease("L1", answering=1)
    assert h.engine.speak_up().run() is None  # quiet, but the session is still at it
    h.store.update_lease("L1", answering=0)
    assert "Acme" in (h.engine.speak_up().run() or "")  # both sides quiet a while: now it is a pause


def test_what_a_deep_talk_left_is_never_brought_up_and_a_line_from_its_words_stays_with_its_project():
    h = make_harness(at(2026, 10, 25, 10))
    h.store.add_lease({"lease_id": "L1", "thread": "t9", "project": "garden", "mode": "l1", "state": "detached",
                       "started": "2026-10-25T08:00:00+08:00"})
    h.engine.messages.receive([{"id": "L1-q0", "project": "garden", "type": "question", "text": "要不要加集水井?", "context": "lease:L1"}])
    h.store.save_thread("t9", {"about": None, "ts": "2026-10-25T09:00:00+08:00", "turns": [
        {"role": "user", "text": "排水", "ts": "2026-10-25T08:00:00+08:00"},
        {"role": "muninn", "text": "跟 garden 談完了:坡度 2%", "ts": "2026-10-25T09:00:00+08:00", "local_only": True, "via": "lease:L1"}],
        "topic": {"title": "後院排水", "state": "waiting", "now": "等你量後院", "ts": "2026-10-25T09:00:00+08:00"},
        "ask": {"text": "後院的長度量好了嗎?", "ts": "2026-10-25T09:00:00+08:00"}})
    talked(h)
    h.clock.t += timedelta(minutes=25)
    assert h.engine.speak_up().run() == "後院的長度量好了嗎?"  # the talk's own question waits in the talk, not here
    last = h.engine.conversation().thread("t9")["turns"][-1]
    assert last["spoken"] and last["local_only"] and last["via"] == ["lease:L1"]


def reminded(h, title, key, ago=timedelta(hours=1)):
    from muninn.core.models import InboxItem

    return h.store.add_inbox(InboxItem(None, h.clock.now() - ago, "health", "alert", title, "", {}, key))


def spoken(h):
    return [x["text"] for t in h.engine.conversation().feed() for x in t["turns"] if x.get("spoken")]


def test_coming_back_after_hours_away_opens_with_the_reminders_and_the_one_thing_that_waits():
    h = make_harness(at(2026, 10, 25, 10), confirm_all=False)
    talked(h)
    h.clock.t += timedelta(hours=4)
    reminded(h, "23:30 之後還在工作", "anomaly:late-night")
    reminded(h, "本週運動落後", "anomaly:gym", ago=timedelta(minutes=30))
    said = h.engine.opened()
    assert said[0].startswith("你不在的時候:本週運動落後、23:30 之後還在工作")  # newest first, in one sentence
    assert len(said) == 2 and said[1] in spoken(h)  # then the one thing that waits most, with its buttons
    assert h.engine.views().alerts() == []  # said, so read
    assert h.channel.sent == []  # the user is looking: nothing pushed
    assert h.engine.opened() == []  # back a moment later: nothing again
    h.clock.t += timedelta(minutes=25)
    assert h.engine.speak_up().run() is None  # the pause before coming back was answered by the opening


def test_the_first_look_of_a_new_day_opens_even_after_a_short_while():
    h = make_harness(at(2026, 10, 25, 22, 30), confirm_all=False)
    talked(h)
    h.clock.t += timedelta(minutes=10)
    assert h.engine.opened() == []  # same evening, a moment away
    h.clock.t += timedelta(hours=2)  # 00:40, the next day
    reminded(h, "23:30 之後還在工作", "anomaly:late-night", ago=timedelta(minutes=50))
    assert h.engine.opened()[0].startswith("你不在的時候:23:30 之後還在工作")


def test_an_opening_names_three_reminders_and_counts_the_rest_and_says_nothing_when_nothing_waits():
    h = make_harness(at(2026, 10, 25, 10))
    for n in range(5):
        reminded(h, f"提醒 {n}", f"anomaly:a{n}", ago=timedelta(minutes=10 * (n + 1)))
    assert h.engine.opened() == ["你不在的時候:提醒 0、提醒 1、提醒 2,還有 2 則提醒。"]
    h.clock.t += timedelta(days=1)
    assert h.engine.opened() == []  # nothing came and nothing waits: no line for the sake of one


def test_what_the_opening_asked_is_not_asked_again_that_day():
    h = make_harness(at(2026, 10, 25, 10), confirm_all=False)
    [first] = h.engine.opened()
    talked(h)
    h.clock.t += timedelta(hours=4)
    assert first not in h.engine.opened()
