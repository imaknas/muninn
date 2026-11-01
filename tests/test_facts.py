from __future__ import annotations

import json

from conftest import ZH, at, make_harness
from muninn.core.models import ExternalTodo


class Records:
    def projects(self):
        return {"home-budget", "resume"}

    def todos(self):
        return [ExternalTodo("home-budget", "me", "問車廠交車日期"), ExternalTodo("resume", "agent", "更新 STATE")]

    def state(self, project):
        return None

    def weekly_stats(self, w):
        return None


def run(h, fact, impact: dict):
    replies = [json.dumps({"actions": [{"tool": "record_fact", "args": {"fact": fact}}], "reply": "記下了"}, ensure_ascii=False),
               json.dumps(impact, ensure_ascii=False)]

    def complete(system, prompt, *, json_mode=False):
        h.local.calls.append((system, prompt))
        return replies.pop(0)

    h.local.complete = complete
    return h.engine.command(fact)


def harness():
    h = make_harness(at(2026, 10, 21, 21))
    h.engine.records = Records()
    h.engine.attr_cfg = type(h.engine.attr_cfg)(rules={**h.engine.attr_cfg.rules, "repos": {**h.engine.attr_cfg.rules["repos"], "resume": "jobs"}},
                                                 sensitive_domains=h.engine.attr_cfg.sensitive_domains)
    return h


def test_fact_resolves_a_deadline_and_hands_off_a_todo():
    h = harness()
    r = run(h, "問到了:2027 Q3 交車", {
        "domain": "jobs", "summary": "交車預計 2027 Q3",
        "effects": [{"id": "deadline:jobs/call-bank", "effect": "resolves", "why": "已經問到了"},
                    {"id": "todo:home-budget:0", "effect": "resolves", "why": "日期問到了"},
                    {"id": "deadline:jobs/nope", "effect": "resolves", "why": "made up"}],
        "projects": ["home-budget", "unknown-project"]})
    texts = [o.text for o in r.outcomes]
    done = next(o for o in r.outcomes if o.action_id)
    assert "Call the bank" in done.text and not any(o.confirm_id for o in r.outcomes)  # done, not asked again
    assert "jobs/call-bank" in h.store.completed()
    h.engine.undo_action(done.action_id)
    assert "jobs/call-bank" not in h.store.completed()
    hand = [x for x in h.engine.handoffs()]
    assert {x["project"] for x in hand} == {"home-budget"}  # the todo's owner; the plan's own deadline stays here
    assert all(x["quote"] == "問到了:2027 Q3 交車" for x in hand)  # the user's words travel along
    assert any("問車廠交車日期" in t for t in texts)
    assert h.cloud.calls == []
    assert all("unknown-project" != x["project"] for x in hand)


def test_fact_moves_a_date_without_asking_again():
    from muninn.core.models import ActionRecord

    h = harness()
    edits = []

    def apply_plan_edit(domain, edit):
        edits.append((domain, edit))
        return h.store.log_action(ActionRecord(None, h.clock.now(), domain, f"期限改成 {edit.due}", {"kind": "test"}))

    h.engine.apply_plan_edit = apply_plan_edit
    r = run(h, "第一批延到下週", {"domain": "jobs", "summary": "延後", "projects": [],
                                  "effects": [{"id": "deadline:jobs/first-batch", "effect": "changes", "why": "延後", "new_due": "2026-10-31"}]})
    [o] = r.outcomes
    assert edits[0][1].due == "2026-10-31" and o.action_id and not o.confirm_id


def test_cancel_handoff():
    h = harness()
    r = run(h, "x", {"domain": "jobs", "summary": "x", "effects": [], "projects": ["home-budget"]})
    hid = next(o.handoff_id for o in r.outcomes if o.handoff_id)
    h.engine.set_handoff_status(hid, "cancelled")
    assert h.engine.handoffs() == []


def test_work_facts_never_leave():
    h = harness()
    r = run(h, "客戶 X 的交車日期改了", {"domain": "work", "summary": "客戶 X 的期限改了",
                                         "effects": [{"id": "todo:home-budget:0", "effect": "relevant", "why": "?"}],
                                         "projects": ["home-budget"]})
    assert h.engine.handoffs() == [] and any("不轉出" in o.text for o in r.outcomes)


def test_open_items_cover_all_sources():

    h = harness()
    ids = {i["id"] for i in h.engine.impact_analyzer().open_items()}
    assert {"deadline:jobs/call-bank", "tracker:jobs/acme", "todo:home-budget:0", "todo:resume:1"} <= ids


def test_same_handoff_is_not_repeated():
    h = harness()
    imp = {"domain": "jobs", "summary": "x", "effects": [], "projects": ["home-budget"]}
    run(h, "x", imp)
    run(h, "x", imp)
    assert len(h.engine.handoffs()) == 1


def test_one_handoff_per_todo_with_the_strongest_effect():
    h = harness()
    run(h, "預期 Q3 交車", {"domain": "jobs", "summary": "預期 Q3 交車", "projects": ["home-budget"],
                         "effects": [{"id": "todo:home-budget:0", "effect": "changes", "why": "前提變了"},
                                     {"id": "todo:home-budget:0", "effect": "resolves", "why": "問到了,到職期限要重算"}]})
    hand = h.engine.handoffs()
    assert len(hand) == 1 and hand[0]["effect"] == "resolves" and "重算" in hand[0]["why"]
    # a later run that only says "changes" does not weaken it
    run(h, "預期 Q3 交車", {"domain": "jobs", "summary": "預期 Q3 交車", "projects": [],
                         "effects": [{"id": "todo:home-budget:0", "effect": "changes", "why": "x"}]})
    assert [x["effect"] for x in h.engine.handoffs()] == ["resolves"]


def test_candidates_keep_own_items_and_the_closest_todos():

    h = harness()
    ids = {i["id"] for i in h.engine.impact_analyzer().candidates("問到交車日期了")}
    assert "todo:home-budget:0" in ids and "todo:resume:1" not in ids
    assert "deadline:jobs/call-bank" in ids  # Muninn's own items always


def test_summary_never_sounds_surer_than_the_user():
    h = harness()
    run(h, "預期 Q3 交車", {"domain": "jobs", "summary": "確認 Q3 交車", "projects": ["home-budget"], "effects": []})
    assert h.engine.handoffs()[0]["fact"] == "預期 Q3 交車"


def test_impact_applier_works_without_an_engine():
    from conftest import FakeClock

    from muninn.adapters.store_sqlite import SqliteStore
    from muninn.core.cases import CaseFeedback
    from muninn.core.facts import Effect, Impact, ImpactApplier
    from muninn.core.handoffs import Handoffs

    class Records:
        def todos(self):
            return [ExternalTodo("b", "me", "問 B")]

    store, clock = SqliteStore(":memory:"), FakeClock(at(2026, 10, 21))
    log = Handoffs(store, clock, CaseFeedback(store, clock))

    def applier(sensitive=()):
        return ImpactApplier(clock, Records(), log, sensitive, lambda domain: None,
                             lambda qid, why: None, lambda domain, edit: None, words=ZH)

    impact = Impact("jobs", "問到了", (Effect("todo:b:0", "relevant", "x"), Effect("todo:b:0", "resolves", "問到了"),
                                      Effect("deadline:jobs/gone", "resolves", "?")), ("b", "c", "muninn"))
    out = applier().apply(impact, quote="原話")
    assert [(h["project"], h["item"], h["effect"], h["quote"]) for h in log.pending()] == [
        ("b", "問 B", "resolves", "原話"), ("c", None, "relevant", "原話")]  # no handoff to Muninn itself
    assert len([o for o in out if o.handoff_id]) == 2
    work = applier({"work"}).apply(Impact("work", "客戶的事", impact.effects, ("d",)))
    assert len(log.pending()) == 2 and all(o.handoff_id is None for o in work)


def test_a_fact_muninn_cannot_place_tells_no_project():
    h = harness()
    run(h, "Claude 時數看 project 判斷", {"domain": None, "summary": "x", "effects": [], "projects": ["home-budget", "resume"]})
    assert h.engine.handoffs() == []


def test_a_resolved_todo_leaves_the_home_list_before_its_project_catches_up():
    h = harness()
    run(h, "問到了:2027 Q3 交車", {"domain": "jobs", "summary": "交車預計 2027 Q3", "projects": [],
                                 "effects": [{"id": "todo:home-budget:0", "effect": "resolves", "why": "問到了"}]})
    listed = [t for projects in h.engine.external_todos().values() for items in projects.values() for t in items]
    assert "問車廠交車日期" not in listed and "更新 STATE" in listed


def test_the_same_task_in_several_lists_is_shown_once():
    h = harness()
    h.local.reply = json.dumps({"same": [{"todo": "home-budget:0", "as": "deadline:jobs/call-bank"},
                                         {"todo": "resume:1", "as": "home-budget:0"},
                                         {"todo": "home-budget:0", "as": "resume:1"}]})  # backwards: ignored
    same = h.engine.match_todos()
    assert same == {"home-budget:0": "deadline:jobs/call-bank", "resume:1": "home-budget:0"}
    calls = len(h.local.calls)
    h.engine.match_todos()
    assert len(h.local.calls) == calls  # lists unchanged: not asked again
    assert h.engine.external_todos() == {}
