from __future__ import annotations

import json
from datetime import date

import pytest

from conftest import CANARY, TZ, at, make_harness
from muninn.adapters.plans_yaml import YamlPlanRepo
from muninn.core import plan_edits
from muninn.core.plan_edits import AddTarget, RemoveTarget, SetDeadlineDue, SetGoalTarget

PLAN = """\
domain: {id: jobs, title: Job search, priority: 1}
# keep this comment
timezone: Asia/Taipei
goals:
- id: golang
  title: Go practice
  metric: hours
  target: 6   # hours a week
  period: week
deadlines:
- id: first-batch
  title: First batch
  due: 2026-10-24
  lead_days: 2
  done_when: manual
targets:
- id: acme
  company: Acme
  role: Staff Engineer
  notes: gap is Go
"""
TRACKER = "- target: acme\n  status: todo\n  next_action: Apply\n"


def yaml_harness(tmp_path):
    d = tmp_path / "jobs"
    d.mkdir()
    (d / "plan.yaml").write_text(PLAN)
    (d / "applications.yaml").write_text(TRACKER)
    h = make_harness(at(2026, 10, 21, 10), plans=[])
    h.engine.plans = YamlPlanRepo(tmp_path, TZ)
    return h, d


def say(h, plan, text="…", answer=None):
    replies = [json.dumps(plan, ensure_ascii=False)] + ([answer] if answer else [])

    def complete(system, prompt, *, json_mode=False):
        h.local.calls.append((system, prompt))
        return replies.pop(0)

    h.local.complete = complete
    return h.engine.command(text)


def test_plan_edits_round_trip(tmp_path):
    h, d = yaml_harness(tmp_path)
    repo = h.engine.plans
    inv = repo.edit("jobs", SetGoalTarget("golang", 4))
    assert "target: 4   # hours a week" in (d / "plan.yaml").read_text() and "# keep this comment" in (d / "plan.yaml").read_text()
    repo.edit("jobs", inv)
    assert repo.load()[0].goal("golang").target == 6
    inv = repo.edit("jobs", AddTarget("globex", "Globex", "Backend", 1, None, "Apply", "2026-10-30"))
    assert repo.load()[0].tracker_item("globex").next_action_due == date(2026, 10, 30)
    repo.edit("jobs", inv)
    assert repo.load()[0].tracker_item("globex") is None
    inv = repo.edit("jobs", RemoveTarget("acme"))
    assert repo.load()[0].target("acme") is None
    repo.edit("jobs", inv)
    assert repo.load()[0].target("acme").notes == "gap is Go"
    inv = repo.edit("jobs", SetDeadlineDue("first-batch", "2026-10-31"))
    assert repo.load()[0].deadlines[0].due == date(2026, 10, 31)
    with pytest.raises(ValueError):
        repo.edit("jobs", SetGoalTarget("nope", 1))
    assert plan_edits.from_dict(plan_edits.to_dict(inv)) == inv


def test_change_plan_is_done_at_once_naming_the_change_with_undo(tmp_path):
    h, _ = yaml_harness(tmp_path)
    r = say(h, {"actions": [{"tool": "change_plan", "args": {"domain": "jobs", "op": "goal_target", "goal": "golang", "target": 4}}],
                "reply": "好"}, "Go 改成每週 4 小時")
    [o] = r.outcomes
    assert o.action_id and not o.confirm_id  # the user's words are the decision
    assert o.text.startswith("[") and "改成每週 4" in o.text and "目前 6" in o.text  # which domain, what, from what
    assert h.engine.plans.load()[0].goal("golang").target == 4
    h.engine.undo_action(o.action_id)
    assert h.engine.plans.load()[0].goal("golang").target == 6


def test_change_plan_without_the_new_value_asks_instead_of_guessing(tmp_path):
    h, _ = yaml_harness(tmp_path)
    r = say(h, {"actions": [{"tool": "change_plan", "args": {"domain": "jobs", "op": "goal_target", "goal": "golang"}}], "reply": ""})
    assert "改成多少" in r.outcomes[0].text and h.engine.plans.load()[0].goal("golang").target == 6


def test_question_answered_locally_with_notes():
    h = make_harness(at(2026, 10, 21, 10))
    h.cloud.reply = "CLOUD"
    r = say(h, {"actions": [{"tool": "ask", "args": {"question": "為什麼要練 Go"}}], "reply": "讓我查"},
            "為什麼是 Go", answer="因為 Acme 的備註寫落差是 Go。")
    assert [o.text for o in r.outcomes] == ["因為 Acme 的備註寫落差是 Go。"] and r.reply == ""
    assert h.cloud.calls == []
    answer_prompt = h.local.calls[-1][1]
    assert CANARY in answer_prompt and "decision_tree" in answer_prompt  # the local model may read everything


def test_feature_request_listed():
    h = make_harness(at(2026, 10, 21, 10))
    r = say(h, {"actions": [{"tool": "request_feature", "args": {"text": "幫我訂明天的高鐵"}}], "reply": ""}, "幫我訂高鐵")
    assert "需求清單" in r.outcomes[0].text
    assert [x["text"] for x in h.engine.feature_requests()] == ["幫我訂明天的高鐵"]


def test_bad_change_is_refused(tmp_path):
    h, _ = yaml_harness(tmp_path)
    r = say(h, {"actions": [{"tool": "change_plan", "args": {"domain": "nope", "op": "goal_target", "goal": "x", "target": 1}},
                            {"tool": "change_plan", "args": {"domain": "jobs", "op": "delete_everything"}}], "reply": ""})
    assert all(o.text.startswith("沒做成") for o in r.outcomes)


def test_change_plan_tolerates_qualified_ids(tmp_path):
    h, _ = yaml_harness(tmp_path)
    r = say(h, {"actions": [{"tool": "change_plan", "args": {"domain": "jobs/golang", "op": "goal_target", "target": 4}}], "reply": ""})
    assert r.outcomes[0].action_id and h.engine.plans.load()[0].goal("golang").target == 4
