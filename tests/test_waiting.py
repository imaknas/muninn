"""What waits on the user is one list, built in one place, in the order it is shown."""

from __future__ import annotations

from datetime import date

from muninn.core import waiting
from muninn.core.waiting import Wait, Waiting

from conftest import ZH, at, make_harness


def test_the_list_keeps_its_sources_order_and_lists_an_item_once():
    w = Waiting([
        lambda: [Wait("topic:t1", "topic", "沙發:等你", about={"kind": "question", "id": 1}, thread="t1", yields=True),
                 Wait("topic:t2", "topic", "窗簾:等你", about=None, thread="t2", yields=True)],
        lambda: [Wait("c:1", "action", "要先問你", about={"kind": "question", "id": 1})],
    ], words=ZH)
    assert [x.id for x in w.all()] == ["topic:t2", "c:1"]  # the conversation about question 1 yields to that question's own card


def test_a_task_waits_from_the_day_before_it_is_due_on_the_users_own_calendar():
    tasks = [{"key": k, "title": k, "due": due} for k, due in (("a", "2026-10-27"), ("b", "2026-10-28"), ("c", "2026-10-20"), ("d", None))]
    got = waiting.due_tasks(lambda: tasks, lambda: date(2026, 10, 26), words=ZH)()
    assert [(x.id, x.says) for x in got] == [("t:a", "a(明天到期)"), ("t:c", "c(過期了)")]


def test_a_deep_talks_questions_wait_together_with_its_conversation_and_a_projects_own_alone():
    qs = [{"id": i, "project": "garden", "text": "紅的還白的?", "context": ctx} for i, ctx in (("q1", None), ("L1-q0", "lease:L1"))]
    got = waiting.project_questions(lambda: qs, words=ZH)()
    assert [(x.id, x.speak, x.via) for x in got] == [("pq:q1", True, ("project:garden",))]
    left = {"lease": "L1", "project": "garden", "called": "花園", "questions": ["坡度?", "集水井?"]}
    threads = [("t9", {"talk_left": left}), ("t8", {"talk_left": left, "lease": "L1"}), ("t7", {})]
    [w] = waiting.talk_questions(lambda: threads, words=ZH)()  # a talk going on has them in hand
    assert (w.id, w.thread, w.lease, w.speak, w.by, w.lines, w.says) == ("talkq:t9", "t9", "L1", False, "花園", ("坡度?", "集水井?"),
                                                                       "花園 的深談留下 2 個問題")


def test_muninn_lists_what_waits_from_what_it_holds():
    h = make_harness(at(2026, 10, 22, 10))
    h.engine.tasks.add("繳房租", "2026-10-23")
    h.engine.messages.receive([{"id": "m1", "project": "garden", "type": "question", "text": "紅的還白的?"}])
    h.store.save_thread("t1", {"about": None, "ts": "2026-10-22T09:00:00+08:00", "turns": [{"role": "user", "text": "沙發", "ts": "x"}],
                               "topic": {"title": "沙發", "state": "waiting", "now": "等你量長度", "ts": "2026-10-22T09:00:00+08:00"},
                               "ask": {"text": "等你量長度", "ts": "2026-10-22T09:00:00+08:00"}})
    ids = [x.id for x in h.engine.waiting()]
    assert "pq:m1" in ids and "topic:t1" in ids and any(i.startswith("t:") for i in ids)
    assert ids.index("topic:t1") < ids.index("pq:m1")  # Muninn's own conversation first, then what projects ask
    asks = h.engine.waiting_asks()
    assert [a.key for a in asks][:2] == ["topic:t1", "pq:m1"] and not any(a.key.startswith("t:") for a in asks)  # tasks never brought up


def test_a_question_left_before_a_deep_talk_is_past_once_the_talk_comes_back(tmp_path):
    """10/25: Muninn asked for photos, the photos and the evening's work went into a deep talk, and the stale
    question was still listed and asked again the next day."""
    import json
    from datetime import timedelta

    from muninn.core.lease import ChannelLease
    from test_lease import FakeRunner, ev

    h = make_harness(at(2026, 10, 25, 14))
    e = h.engine
    e.leases = ChannelLease(e.store, h.clock, FakeRunner(), h.channel, lambda now: False, lambda tid: e.conversation().thread(tid),
                            lambda tid, change: e.conversation().update_thread(tid, change), ended=lambda lease, report: e.talk_ended(lease, report), words=ZH)
    h.cloud.reply = h.local.reply = json.dumps({"actions": [], "reply": "你把圖片貼過來。",
                                                "topic": {"title": "房間 3D", "state": "open", "now": "等你貼照片"}, "question_for_him": "等你貼照片"}, ensure_ascii=False)
    c = e.conversation()
    tid = c.execute_turn(c.understand_turn(None, "我要給圖片")) and c.feed()[-1]["thread"]
    assert [w.id for w in e.waiting()] == [f"topic:{tid}"]
    lid = e.leases.attach(tid, "garden")["lease_id"]
    h.clock.t += timedelta(hours=8)
    e.leases.on_event(ev(lid, 1, "handover", handover={"summary": "照片收到,場景做到 v32"}))
    e.leases.on_event(ev(lid, 2, "state", state="detached"))
    assert e.waiting() == [] and e.waiting_asks() == []  # nothing left to ask; not in 在做 either
    assert e.topics().today()[0]["state"] == "open"
    h.clock.t += timedelta(hours=1)
    assert "等你貼照片" not in "".join(e.engine.still_open())  # nor in the morning
    [done] = [d for d in e.home_view()["done"] if "等你貼照片" in d["text"]]
    assert e.undo_action(done["id"]) and [w.id for w in e.waiting()] == [f"topic:{tid}"]  # taken back: asked again


def test_a_to_do_a_deep_talks_later_handover_no_longer_lists_stays_the_users():
    from datetime import timedelta

    from muninn.core.lease import ChannelLease
    from test_lease import FakeRunner, ev

    h = make_harness(at(2026, 10, 25, 14))
    e = h.engine
    e.leases = ChannelLease(e.store, h.clock, FakeRunner(), h.channel, lambda now: False, lambda tid: e.conversation().thread(tid),
                            lambda tid, change: e.conversation().update_thread(tid, change), ended=lambda lease, report: e.talk_ended(lease, report), words=ZH)
    got = e.leases.attach(None, "garden")
    lid = got["lease_id"]

    def handover(seq, n, todos):
        e.leases.on_event(ev(lid, seq, "handover", handover={"summary": "排水", "tasks_for_owner": [
            {"text": text, "key": f"lease-{lid}-{n}-task-{k}"} for k, text in enumerate(todos)]}))
        e.leases.on_event(ev(lid, seq + 1, "state", state="detached"))
        e.messages.receive([{"id": f"lease-{lid}-{n}-task-{k}", "project": "garden", "type": "task", "text": text, "context": f"lease:{lid}"}
                            for k, text in enumerate(todos)])

    handover(1, 1, ["拍後院照片", "量後院長度"])
    own = e.tasks.add("買水管")  # not the talk's: untouched
    e.leases.attach(got["thread"], "garden")
    h.clock.t += timedelta(hours=3)
    handover(3, 2, ["決定要不要集水井"])
    # a requester that stops listing a to-do says nothing about it: only a claim or a withdrawal changes it
    assert sorted(t["title"] for t in e.tasks.open()) == ["拍後院照片", "決定要不要集水井", "買水管", "量後院長度"]
    assert not [d for d in e.home_view()["done"] if "拍後院照片" in d["text"]] and own["key"] in [t["key"] for t in e.tasks.open()]


def _question_talk(h):
    from muninn.core.lease import ChannelLease
    from test_lease import FakeRunner

    e = h.engine
    e.leases = ChannelLease(e.store, h.clock, FakeRunner(), h.channel, lambda now: False, lambda tid: e.conversation().thread(tid),
                            lambda tid, change: e.conversation().update_thread(tid, change), ended=lambda lease, report: e.talk_ended(lease, report), words=ZH)
    e.messages.receive([{"id": "m7", "project": "garden", "type": "question", "text": "要不要做集水井?\n\n背景:後院排水。"}])
    e.conversation().update_thread("t1", lambda t: {"about": {"kind": "project_question", "id": "m7"}, "turns": []})
    return e, e.leases.attach("t1", "garden")["lease_id"]


def test_a_question_decided_in_a_talk_with_the_project_leaves_his_list_with_nothing_sent_back():
    from test_lease import ev

    h = make_harness(at(2026, 10, 25, 14))
    e, lid = _question_talk(h)
    assert "pq:m7" in [w.id for w in e.waiting()]
    e.leases.on_event(ev(lid, 1, "handover", handover={"summary": "談了集水井", "answered": {"question": "m7", "answer": "先不做", "closed": True}}))
    e.leases.on_event(ev(lid, 2, "state", state="detached"))
    assert e.messages.open_questions() == [] and e.handoffs() == []  # the platform closed it as the project
    # the platform's 'done' for it arrives after: he is not asked whether he decided what he just decided
    e.messages.claimed("m7", "garden", {"id": "c:m7", "text": "先不做"})
    e.messages.withdrawn("m7", "garden", "settled")
    assert e.waiting() == [] and e.store.confirmations() == []


def test_a_question_whose_done_came_before_the_handover_is_not_asked_about():
    from test_lease import ev

    h = make_harness(at(2026, 10, 25, 14))
    e, lid = _question_talk(h)
    e.messages.claimed("m7", "garden", {"id": "c:m7", "text": "先不做"})
    e.messages.withdrawn("m7", "garden", "settled")
    assert [c.kind for c in e.store.confirmations()] == ["decision_claim"]
    e.leases.on_event(ev(lid, 1, "handover", handover={"summary": "談了集水井", "answered": {"question": "m7", "answer": "先不做", "closed": True}}))
    e.leases.on_event(ev(lid, 2, "state", state="detached"))
    assert e.store.confirmations() == [] and e.handoffs() == []


def test_an_answer_the_platform_could_not_send_leaves_the_question_waiting():
    from test_lease import ev

    h = make_harness(at(2026, 10, 25, 14))
    e, lid = _question_talk(h)
    e.leases.on_event(ev(lid, 1, "handover", handover={"summary": "談了集水井", "answered": {"question": "m7", "answer": "先不做", "closed": False}}))
    e.leases.on_event(ev(lid, 2, "state", state="detached"))
    assert [q["id"] for q in e.messages.open_questions()] == ["m7"]
