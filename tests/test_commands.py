from __future__ import annotations

import json

from conftest import at, make_harness
from test_actions import FakeCalendar


def say(h, plan: dict, text: str = "…"):
    h.local.reply = json.dumps(plan, ensure_ascii=False)
    return h.engine.command(text)


def harness():
    h = make_harness(at(2026, 10, 22, 10))
    h.cloud.reply = "CLOUD SHOULD NOT BE USED"
    return h


def test_status_change_and_undo_never_cloud():
    h = harness()
    r = say(h, {"actions": [{"tool": "set_status", "args": {"target": "jobs/acme", "status": "applied"}}], "reply": "好"},
            "我剛投了 Acme")
    [o] = r.outcomes
    assert h.plans.plans["jobs"].tracker_item("acme").status == "applied" and o.action_id
    assert h.engine.undo_action(o.action_id) and h.plans.plans["jobs"].tracker_item("acme").status == "todo"
    assert h.cloud.calls == [] and "我剛投了 Acme" in h.local.calls[0][1]
    notes = h.store.observations(at(2026, 10, 22, 0), kinds=("note",))
    assert notes[0].payload["text"] == "我剛投了 Acme"


def test_hours_and_undo():
    h = harness()
    r = say(h, {"actions": [{"tool": "log_hours", "args": {"goal": "jobs/golang", "hours": 1.5, "date": None}}], "reply": ""})
    goals = lambda: {g["id"]: g for g in h.engine.today_view()["domains"][0]["goals"]}  # noqa: E731
    assert goals()["golang"]["actual"] == 1.5
    h.engine.undo_action(r.outcomes[0].action_id)
    assert goals()["golang"]["actual"] == 0


def test_mark_done_and_progress():
    h = harness()
    r = say(h, {"actions": [{"tool": "mark_done", "args": {"deadline": "jobs/call-bank"}},
                            {"tool": "progress", "args": {"goal": "jobs/apply"}}], "reply": "好"})
    assert "jobs/call-bank" in h.store.completed()
    assert r.outcomes[1].text.startswith("Weekly applications")
    h.engine.undo_action(r.outcomes[0].action_id)
    assert "jobs/call-bank" not in h.store.completed()


def test_schedule_move_cancel_with_undo():
    h = harness()
    cal = FakeCalendar(busy=[(at(2026, 10, 23, 9), at(2026, 10, 23, 12))])
    h.engine.calendar = cal
    r = say(h, {"actions": [{"tool": "schedule", "args": {"title": "寫履歷", "date": "2026-10-23", "start": None, "minutes": 60}}], "reply": ""})
    [ev] = cal.made.values()
    assert ev.title == "Muninn · 寫履歷" and ev.start >= at(2026, 10, 23, 12)
    r2 = say(h, {"actions": [{"tool": "move_block", "args": {"block": ev.id, "date": "2026-10-24", "start": "10:00"}}], "reply": ""})
    [moved] = cal.made.values()
    assert moved.start == at(2026, 10, 24, 10)
    h.engine.undo_action(r2.outcomes[0].action_id)
    [back] = cal.made.values()
    assert back.start == ev.start
    r3 = say(h, {"actions": [{"tool": "cancel_block", "args": {"block": back.id}}], "reply": ""})
    assert cal.made == {}
    h.engine.undo_action(r3.outcomes[0].action_id)
    assert len(cal.made) == 1
    assert r.outcomes[0].action_id


def test_schedule_respects_earliest():
    h = harness()
    cal = FakeCalendar()
    h.engine.calendar = cal
    say(h, {"actions": [{"tool": "schedule", "args": {"title": "寫履歷", "date": "2026-10-23", "start": None, "earliest": "13:00", "minutes": 60}}], "reply": ""})
    [ev] = cal.made.values()
    assert ev.start == at(2026, 10, 23, 13)


def test_unknown_ids_do_nothing():
    h = harness()
    r = say(h, {"actions": [{"tool": "set_status", "args": {"target": "jobs/nope", "status": "applied"}},
                            {"tool": "rm_rf", "args": {}}], "reply": ""})
    assert [o.text for o in r.outcomes] == ["沒做成:找不到 jobs/nope"]  # unknown tools are dropped
    assert h.plans.plans["jobs"].tracker_item("acme").status == "todo"


def test_garbage_or_local_down_keeps_the_note():
    h = harness()
    h.local.reply = "I cannot"
    r = h.engine.command("今天好累")
    assert not r.understood and "筆記" in r.reply
    h.local.up = False
    r = h.engine.command("還是好累")
    assert not r.understood and h.cloud.calls == []
    assert len(h.store.observations(at(2026, 10, 22, 0), kinds=("note",))) == 2


def test_venting_needs_no_action():
    h = harness()
    r = say(h, {"actions": [], "reply": "辛苦了,今晚早點休息。"}, "今天好累")
    assert r.reply == "辛苦了,今晚早點休息。" and r.outcomes == []


def test_context_offers_real_ids():
    h = harness()

    ctx = h.engine.commands().context()
    assert {"id": "jobs/acme", "title": "Acme Staff Engineer", "status": "todo"} in ctx["targets"]
    assert any(g["id"] == "jobs/golang" for g in ctx["goals"])
    assert ctx["today"].startswith("2026-10-22 (Thu)")


def test_command_api(web):
    h, client, devices = web
    client.post("/api/pair", json={"code": devices.new_pairing_code()})
    h.local.reply = json.dumps({"actions": [{"tool": "today", "args": {}}], "reply": "今天先做這些"})
    r = client.post("/api/command", json={"text": "今天做什麼"}).json()
    assert r["reply"] == "" and r["outcomes"][0]["link"] == "#home"  # a lookup answers with its result


def test_the_model_reads_the_same_tool_list_and_rules():
    """Moving the tools into their own classes must not change a word of what the model is told."""
    from pathlib import Path

    from muninn.core.commands import system
    from muninn.languages import language

    from muninn.core.apply import Sites

    h = make_harness(at(2026, 10, 22, 10))
    h.engine.leases = object()  # with a session runner and a site to fill forms on: every tool there is
    h.engine.applications.sites = Sites([object()])
    golden = (Path(__file__).parent / "golden" / "command_prompt.txt").read_text()
    assert system(language("zh-TW"), h.engine.commands().tools.tool_list()) == golden


def test_the_english_routing_prompt_differs_only_in_the_language_it_asks_for_and_how_short_labels_are():
    from muninn.core.commands import system
    from muninn.languages import language

    h = make_harness(at(2026, 10, 22, 10), lang="en")
    h.engine.leases = object()
    tools = h.engine.commands().tools.tool_list()
    zh, en = language("zh-TW"), language("en")
    assert system(en, tools) != system(zh, tools)
    as_english = system(zh, tools).replace(zh.name, en.name)
    for key in ("commands.label_length", "commands.title_length"):
        as_english = as_english.replace(zh(key), en(key))
    assert system(en, tools) == as_english


def test_talking_to_a_project_is_offered_only_when_a_session_can_take_the_talk_over():
    h = make_harness(at(2026, 10, 22, 10))
    assert "talk_to_project" not in h.engine.commands().tools.names  # no session runner: nothing would answer
    h.engine.leases = object()
    assert "talk_to_project" in h.engine.commands().tools.names


def test_a_moved_block_may_land_on_its_own_old_time_when_the_calendar_merges_busy_times():
    """Google's free/busy merges back-to-back events into one interval; the block's own time still frees up."""
    class MergingCalendar(FakeCalendar):
        def busy(self, start, end):
            out = []
            for s, e in sorted(super().busy(start, end)):
                if out and s <= out[-1][1]:
                    out[-1] = (out[-1][0], max(out[-1][1], e))
                else:
                    out.append((s, e))
            return out

    h = harness()
    cal = MergingCalendar(busy=[(at(2026, 10, 23, 13), at(2026, 10, 23, 14))])
    h.engine.calendar = cal
    say(h, {"actions": [{"tool": "schedule", "args": {"title": "寫履歷", "date": "2026-10-23", "start": "12:00", "minutes": 60}}], "reply": ""})
    [ev] = cal.made.values()
    say(h, {"actions": [{"tool": "move_block", "args": {"block": ev.id, "date": "2026-10-23", "start": None, "earliest": "11:30"}}], "reply": ""})
    [moved] = cal.made.values()
    assert moved.start == at(2026, 10, 23, 11, 30)  # over half its old hour, which counts as free


def lent(h, lease_id, project, thread):
    """A deep talk with `project`, as the lease store keeps one."""
    now = h.clock.now().isoformat()
    h.store.add_lease({"lease_id": lease_id, "thread": thread, "project": project, "mode": "discuss", "state": "ready",
                       "started": now, "last_activity": now, "last_user_at": now})


def beside_round(h, plan, said, history=()):
    """Muninn's part of a sentence said in a deep talk with garden, understood beside it."""
    lent(h, "L1", "garden", "talk")
    h.local.reply = json.dumps(plan, ensure_ascii=False)
    return h.engine.commands().understand(said, history=list(history), talk=True,
                                          beside={"lease": "L1", "project": "garden", "called": "garden", "talk": []})


SANDBOX = "沙盒先只放行 seeds.example 和 *.seeds.example;要登入的那一步在他自己的電腦上用有畫面的瀏覽器做。"


def test_an_answer_beside_a_talk_keeps_the_sentence_for_its_project():
    h = harness()
    plan = beside_round(h, {"actions": [], "reply": "先開 seeds.example 和 *.seeds.example 就夠。登入在你自己的電腦上做。",
                            "for_project": SANDBOX}, "我想跟 Muninn 討論沙盒最少要放行哪些網站")
    assert plan["for_project"] == SANDBOX
    asked = beside_round(h, {"actions": [{"tool": "ask", "args": {"question": "沙盒先開哪些網域?他週五有空測嗎?"}}], "reply": "",
                             "for_project": "沙盒先開 seeds.example。"}, "沙盒先開哪些網域?我週五有空測嗎?")
    assert asked["for_project"] == "沙盒先開 seeds.example。"  # only looked something up: still said


def test_the_projects_sentence_is_dropped_without_a_talk_or_when_the_round_did_something():
    h = harness()
    h.local.reply = json.dumps({"actions": [], "reply": "好", "for_project": SANDBOX}, ensure_ascii=False)
    assert h.engine.commands().understand("沙盒要開哪些?")["for_project"] is None  # no talk beside: no one to tell
    plan = beside_round(h, {"actions": [{"tool": "add_task", "args": {"title": "看牙醫", "due": "2026-10-23"}}], "reply": "好",
                            "for_project": SANDBOX}, "提醒我明天看牙醫,順便問沙盒開哪些網域")
    assert plan["for_project"] is None and plan["actions"]  # done for him: nothing of that round goes to the project


def test_the_projects_sentence_is_dropped_when_the_round_read_another_projects_talk():
    h = harness()
    lent(h, "L2", "blog", "talk")
    history = [{"role": "muninn", "text": "跟 blog 談完了:草稿下週改。", "ts": "2026-10-22T09:00", "local_only": True, "via": "lease:L2"}]
    plan = beside_round(h, {"actions": [], "reply": "先開 seeds.example。", "for_project": SANDBOX}, "沙盒先開哪些?", history)
    assert plan["for_project"] is None and plan["local_only"]


def test_the_projects_sentence_is_one_sentence_at_most_three_hundred_characters():
    h = harness()
    plan = beside_round(h, {"actions": [], "reply": "好", "for_project": "網" * 400}, "沙盒要開哪些?")
    assert plan["for_project"] == "網" * 300


def test_news_beside_a_talk_that_settles_something_keeps_the_round_from_the_project():
    h = harness()
    lent(h, "L1", "garden", "talk")
    replies = [{"actions": [{"tool": "record_fact", "args": {"fact": "銀行回電了"}}], "reply": "好", "for_project": SANDBOX},
               {"domain": "jobs", "summary": "銀行回電了", "effects": [{"id": "deadline:jobs/call-bank", "effect": "resolves", "why": "回電了"}],
                "projects": []}]
    h.local.complete = lambda system, prompt, *, json_mode=False: json.dumps(replies.pop(0), ensure_ascii=False)
    plan = h.engine.commands().understand("銀行回電了,順便問沙盒開哪些", talk=True,
                                          beside={"lease": "L1", "project": "garden", "called": "garden", "talk": []})
    assert plan["impact"].effects and plan["for_project"] is None
