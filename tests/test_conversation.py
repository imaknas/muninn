from __future__ import annotations

import json

from conftest import ZH, at, make_harness


def replies(h, *texts):
    queue = [json.dumps(t, ensure_ascii=False) if isinstance(t, dict) else t for t in texts]

    def complete(system, prompt, *, json_mode=False):
        h.local.calls.append((system, prompt))
        return queue.pop(0)

    h.local.complete = complete


def unreviewed():
    return make_harness(at(2026, 10, 21, 21), confirm_all=False)


def test_a_thread_remembers_what_was_said():
    h = make_harness(at(2026, 10, 21, 21))
    replies(h, {"actions": [], "reply": "你是說哪一個?"}, {"actions": [], "reply": "好,了解"})
    first = h.engine.conversation().understand_turn(None, "那個目標太多了", None)
    h.engine.conversation().execute_turn(first)
    second = h.engine.conversation().understand_turn(first["thread"], "練 Go 那個", None)
    h.engine.conversation().execute_turn(second)
    assert second["thread"] == first["thread"]
    prompt = json.loads(h.local.calls[-1][1])
    assert [t["text"] for t in prompt["conversation"]] == ["那個目標太多了", "你是說哪一個?"]


def test_talk_without_news_does_not_scan_every_todo():
    h = make_harness(at(2026, 10, 21, 21))
    replies(h, {"actions": [], "reply": "嗯"})
    h.engine.conversation().execute_turn(h.engine.conversation().understand_turn(None, "為什麼要這個", {"kind": "review", "id": "x"}))
    assert len(h.local.calls) == 1


def test_discussing_a_review_item_then_deciding():
    h = unreviewed()
    item = h.engine.review_queue()[0]
    replies(h, {"actions": [], "reply": "這是規劃時推出來的。"},
            {"actions": [{"tool": "decide_item", "args": {"decision": "confirm"}}], "reply": "好,就這樣"})
    about = {"kind": "review", "id": item["id"]}
    t1 = h.engine.conversation().understand_turn(None, "這是哪來的?", about)
    h.engine.conversation().execute_turn(t1)
    assert item["id"] in [x["id"] for x in h.engine.review_queue()]  # asking decides nothing
    assert json.loads(h.local.calls[0][1])["about"]["title"] == item["title"]
    r2 = h.engine.conversation().execute_turn(h.engine.conversation().understand_turn(t1["thread"], "那就要", None))
    assert item["id"] not in [x["id"] for x in h.engine.review_queue()]
    assert r2.outcomes[0].action_id  # undoable


def test_answering_a_question_in_a_discussion():
    from muninn.core.models import Confirmation

    h = make_harness(at(2026, 10, 21, 21))
    c = h.store.add_confirmation(Confirmation(None, h.clock.now(), "fact_done", "「Call the bank」這件完成了?", ("是", "不是"),
                                              {"deadline": "jobs/call-bank"}, key="k"))
    replies(h, {"actions": [{"tool": "answer_question", "args": {"answer": "是"}}], "reply": "好"})
    r = h.engine.conversation().execute_turn(h.engine.conversation().understand_turn(None, "對,問完了", {"kind": "question", "id": str(c.id)}))
    assert "jobs/call-bank" in h.store.completed() and "是" in r.outcomes[0].text


def test_discussing_a_project_question_then_answering_it():
    h = make_harness(at(2026, 10, 21, 21))
    h.engine.messages.receive([{"id": "q1", "project": "resume", "type": "question", "text": "最晚到職日要用 Q3 重算嗎?"}])
    replies(h, {"actions": [], "reply": "看你車貸核准的時間。"},
            {"actions": [{"tool": "answer_question", "args": {"answer": "要,用 2027 Q3 交車重算"}}], "reply": "好,轉給 resume"})
    t1 = h.engine.conversation().understand_turn(None, "這要看什麼?", {"kind": "project_question", "id": "q1"})
    h.engine.conversation().execute_turn(t1)
    about = json.loads(h.local.calls[0][1])["about"]
    assert about["project"] == "resume" and "Q3" in about["question"]
    assert h.engine.messages.open_questions()  # talking answers nothing
    r = h.engine.conversation().execute_turn(h.engine.conversation().understand_turn(t1["thread"], "要,用 Q3 重算", None))
    assert not h.engine.messages.open_questions()
    [handoff] = [x for x in h.engine.handoffs() if x.get("reply_to") == "q1"]
    assert handoff["project"] == "resume" and "Q3" in handoff["fact"] and r.outcomes[0].handoff_id == handoff["id"]


def test_a_discussion_leaves_next_steps_that_one_tap_carries_out():
    h = unreviewed()
    item = h.engine.review_queue()[0]
    replies(h, {"actions": [], "reply": "這個期限是規劃時推的;你沒時間的話可以先不要。",
                "suggest": [{"label": "要這個期限", "tool": "decide_item", "args": {"decision": "confirm"}},
                            {"label": "看今天", "tool": "today", "args": {}}]})
    about = {"kind": "review", "id": item["id"]}
    t = h.engine.conversation().understand_turn(None, "這個有必要嗎?", about)
    r = h.engine.conversation().execute_turn(t)
    assert [x["label"] for x in r.suggest] == ["要這個期限"]  # lookups are not next steps
    card = next(x for x in h.engine.review_queue() if x["id"] == item["id"])
    assert card["talk"]["suggest"][0]["label"] == "要這個期限" and "規劃時" in card["talk"]["last"]
    done = h.engine.conversation().act(t["thread"], 0)
    assert done.outcomes[0].action_id and item["id"] not in [x["id"] for x in h.engine.review_queue()]


def test_the_model_sees_the_whole_plan_of_the_item_under_discussion():
    h = unreviewed()
    item = next(x for x in h.engine.review_queue() if x["domain"] == "jobs")
    replies(h, {"actions": [], "reply": "嗯"})
    h.engine.conversation().execute_turn(h.engine.conversation().understand_turn(None, "這幾間都要 Go?", {"kind": "review", "id": item["id"]}))
    dossier = json.loads(h.local.calls[0][1])["dossier"]
    assert dossier["plan"]["domain"] == "jobs" and dossier["plan"]["targets"] and "waiting_for_review" in dossier


def test_with_the_dossier_at_hand_it_is_answered_from_that_not_looked_up_again():
    h = unreviewed()
    item = next(x for x in h.engine.review_queue() if x["domain"] == "jobs")
    replies(h, {"actions": [{"tool": "ask", "args": {"question": "為什麼要 Go?"}}], "reply": "計畫裡寫了要 Go。"})
    r = h.engine.conversation().execute_turn(h.engine.conversation().understand_turn(None, "這幾間都要 Go?", {"kind": "review", "id": item["id"]}))
    system = h.local.calls[0][0]
    assert "- ask:" not in system and "dossier" in json.loads(h.local.calls[0][1])  # not offered
    assert r.outcomes == [] and r.reply == "計畫裡寫了要 Go。"  # and not done if asked for anyway


def test_a_step_already_done_is_not_offered_again():
    from muninn.core.commands import parse

    got = parse(json.dumps({"actions": [{"tool": "close_task", "args": {"task": "T-1", "status": "done"}}], "reply": "",
                            "suggest": [{"label": "完成", "tool": "close_task", "args": {"task": "T-1", "status": "done"}},
                                        {"label": "改日期", "tool": "set_task_due", "args": {"task": "T-1", "due": "2026-11-01"}}]}), words=ZH)
    assert [s["label"] for s in got["suggest"]] == ["改日期"]


def answer_of(h, cid):
    return next(x.answer for x in h.store.confirmations(pending_only=False) if x.id == cid)


def asked(h):
    """Muninn's question about a packet, said in its conversation with its buttons (as speaking up says it)."""
    from muninn.core.actions import DecidedBy, TrackerStatus

    h.engine.gate.run(TrackerStatus("jobs", "acme", "interview", "Acme 寄來面試邀請"), DecidedBy.INFERRED_UNSURE)
    [c] = [c for c in h.engine.pending_confirmations() if c.kind == "action"]
    buttons = [{"label": o, "tool": "answer_question", "args": {"answer": o}} for o in c.options]
    return c, buttons


def test_a_question_said_again_the_next_day_has_its_buttons_once_and_one_press_answers_it():
    from datetime import timedelta

    h = make_harness(at(2026, 10, 21, 15))
    c, buttons = asked(h)
    conv = h.engine.conversation()
    tid = conv.speak({"kind": "question", "id": c.id}, c.question, buttons)
    h.clock.t += timedelta(hours=11)  # still open after midnight: said again
    assert conv.speak({"kind": "question", "id": c.id}, c.question, buttons) == tid
    [shown] = [t for t in conv.feed() if t["thread"] == tid]
    latest = shown["turns"][-1]["ts"]
    assert [(s["label"], s["turn"]) for s in shown["suggest"]] == [(o, latest) for o in c.options]  # under the latest only
    conv.act(tid, button=shown["suggest"][1]["id"])
    assert answer_of(h, c.id) == c.options[1]
    [after] = [t for t in conv.feed() if t["thread"] == tid]
    assert after["suggest"] == []


def test_a_question_only_brought_up_leaves_its_card_nothing_after_a_discussion():
    h = make_harness(at(2026, 10, 21, 15))
    c, buttons = asked(h)
    conv = h.engine.conversation()
    conv.speak({"kind": "question", "id": c.id}, c.question, buttons)
    assert conv.suggestions_for("question", c.id) is None  # its card has the question and its answers once


def test_a_discussed_question_said_again_shows_on_its_card_what_the_discussion_left_not_the_question_again():
    from datetime import timedelta

    h = make_harness(at(2026, 10, 21, 15))
    c, buttons = asked(h)
    conv = h.engine.conversation()
    about = {"kind": "question", "id": str(c.id)}
    replies(h, {"actions": [], "reply": "看你那段時間在哪個視窗。"})
    conv.execute_turn(conv.understand_turn(None, "這要怎麼判斷?", about))
    h.clock.t += timedelta(hours=11)  # still open after midnight: said again
    conv.speak(about, c.question, buttons)
    talk = conv.suggestions_for("question", c.id)
    assert talk["last"] == "看你那段時間在哪個視窗。"
    assert talk["suggest"] == []  # not the card's own answers a second time


def test_buttons_already_drawn_twice_all_go_with_one_press():
    from datetime import timedelta

    h = make_harness(at(2026, 10, 21, 15))
    c, buttons = asked(h)
    conv = h.engine.conversation()
    tid = conv.speak({"kind": "question", "id": c.id}, c.question, buttons)
    h.clock.t += timedelta(hours=11)
    t = conv.thread(tid)  # kept as an older Muninn kept a question said twice: both lines with their buttons
    t["turns"].append({"role": "muninn", "text": c.question, "ts": h.clock.now().isoformat(), "spoken": True})
    t["suggest"] += [{**b, "turn": h.clock.now().isoformat()} for b in buttons]
    conv.save_thread(tid, t)
    conv.act(tid, 1)
    assert conv.thread(tid)["suggest"] == []


def test_a_question_answered_on_its_card_leaves_no_button_in_the_conversation_and_a_stale_press_does_nothing():
    import pytest

    h = make_harness(at(2026, 10, 21, 15))
    c, buttons = asked(h)
    conv = h.engine.conversation()
    tid = conv.speak({"kind": "question", "id": c.id}, c.question, buttons + [{"label": "記下來", "tool": "capture_idea", "args": {"idea": "x"}}])
    [shown] = [t for t in conv.feed() if t["thread"] == tid]
    no = next(s for s in shown["suggest"] if s["label"] == c.options[1])
    h.engine.answer(c.id, c.options[0])
    [after] = [t for t in conv.feed() if t["thread"] == tid]
    assert [s["label"] for s in after["suggest"]] == ["記下來"]  # what does not answer the question stays
    with pytest.raises(ValueError, match=ZH("conversation.suggestion_gone")):
        conv.act(tid, button=no["id"])
    assert answer_of(h, c.id) == c.options[0]


def test_a_press_names_its_button_so_a_list_changed_meanwhile_presses_nothing_else():
    import pytest

    h = make_harness(at(2026, 10, 21, 15))
    conv = h.engine.conversation()
    tid = conv.speak(None, "上線了:「澆水摘要」", [{"label": "還沒好", "tool": "capture_idea", "args": {"idea": "a"}}])
    [first] = conv.feed()[-1]["suggest"]
    replies(h, {"actions": [], "reply": "好", "suggest": [{"label": "記下來", "tool": "capture_idea", "args": {"idea": "b"}}]})
    conv.execute_turn(conv.understand_turn(tid, "嗯"))  # a typed round: its own next steps, in place of the earlier ones
    with pytest.raises(ValueError):
        conv.act(tid, button=first["id"])  # not 記下來, which is now where it was
    assert [s["label"] for s in conv.feed()[-1]["suggest"]] == ["記下來"]


def test_what_muninn_says_while_a_round_is_being_carried_out_stays_in_the_conversation():
    h = make_harness(at(2026, 10, 21, 15))
    conv = h.engine.conversation()
    tid = conv.speak(None, "上線了:「澆水摘要」", [])
    replies(h, {"actions": [{"tool": "capture_idea", "args": {"idea": "週五繳電費"}}], "reply": "記下了"})
    turn = conv.understand_turn(tid, "記一下週五繳電費")
    runner = conv._commands()
    execute = runner.execute

    def meanwhile(plan):  # a project's answer arriving (said by the scheduled run) while this round runs
        conv.speak(None, "garden 回覆了:排水坡度 2%", [], thread=tid)
        return execute(plan)

    runner.execute = meanwhile
    conv._commands = lambda: runner
    conv.execute_turn(turn)
    said = [x["text"] for x in conv.thread(tid)["turns"]]
    assert "garden 回覆了:排水坡度 2%" in said and "記一下週五繳電費" in said and said[0] == "上線了:「澆水摘要」"
