"""Plan suggestions stay quiet until the user confirms them."""

from __future__ import annotations

from conftest import at, make_harness
from test_orchestrator import yaml_harness


def test_unconfirmed_goals_and_deadlines_are_quiet():
    h = make_harness(at(2026, 10, 23, 20), confirm_all=False)
    assert h.engine.evaluate().items == ()  # behind on goals, deadline in a day: but nothing confirmed
    assert h.engine.home_view()["next"] == [] or all(q["kind"] == "next_action" for q in h.engine.home_view()["next"])
    queue = h.engine.review_queue()
    assert [q["title"] for q in queue][:2] == ["Weekly applications", "Go practice"]
    [brief] = h.engine.morning()
    assert brief.data["push_title"].startswith("早安 · 有") and "Weekly applications" in brief.data["push_title"]


def test_confirm_then_it_acts_and_undo_quiets_again():
    h = make_harness(at(2026, 10, 23, 20), confirm_all=False)
    rec = h.engine.review("jobs/goal:apply", "confirm")
    assert "jobs:goal:apply" in {i.signal_key for i in h.engine.evaluate().items}
    assert "jobs/goal:apply" not in [q["id"] for q in h.engine.review_queue()]
    h.engine.undo_action(rec.id)
    assert "jobs/goal:apply" in [q["id"] for q in h.engine.review_queue()]


def test_change_and_reject_write_the_plan_with_undo(tmp_path):
    h, d = yaml_harness(tmp_path)
    assert {q["id"] for q in h.engine.review_queue()} == {"jobs/goal:golang", "jobs/deadline:first-batch"}
    rec = h.engine.review("jobs/goal:golang", "change", 2)
    assert h.engine._plan("jobs").goal("golang").target == 2  # confirmed, so acted on
    h.engine.undo_action(rec.id)
    assert h.engine._plan("jobs", include_unconfirmed=True).goal("golang").target == 6
    assert h.engine._plan("jobs").goal("golang") is None
    rec = h.engine.review("jobs/goal:golang", "reject")
    assert h.engine._plan("jobs", include_unconfirmed=True).goal("golang") is None
    assert "golang" not in (d / "plan.yaml").read_text()
    h.engine.undo_action(rec.id)
    assert h.engine._plan("jobs", include_unconfirmed=True).goal("golang").title == "Go practice"


def test_why_is_answered_locally():
    h = make_harness(at(2026, 10, 23, 20), confirm_all=False)
    h.local.reply = "這是規劃時根據職缺推論的,沒有你說過的紀錄。"
    assert "推論" in h.engine.review_why("jobs/goal:golang")
    assert h.cloud.calls == []
    assert "Go practice" in h.local.calls[-1][1]


def test_review_api(web):
    h, client, devices = web
    client.post("/api/pair", json={"code": devices.new_pairing_code()})
    h.store.set_state("plan_review", {})
    items = client.get("/api/review").json()["items"]
    r = client.post("/api/review", json={"id": items[0]["id"], "decision": "confirm"}).json()
    assert r["ok"] and r["action_id"]
    assert len(client.get("/api/review").json()["items"]) == len(items) - 1


def test_source_shown_in_review(tmp_path):
    h, d = yaml_harness(tmp_path)
    txt = (d / "plan.yaml").read_text().replace("  period: week\n", "  period: week\n  source: 推論:依職缺需求\n", 1)
    (d / "plan.yaml").write_text(txt)
    item = next(q for q in h.engine.review_queue() if q["id"] == "jobs/goal:golang")
    assert item["source"] == "推論:依職缺需求"
