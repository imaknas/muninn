"""What counts toward a domain, said in words: repos, apps and named places, undoable. Invented data."""

from __future__ import annotations

import json
from datetime import timedelta

from conftest import at, make_harness
from muninn.core.models import Observation, Sensitivity


def say(h, args, said="這些算 jobs"):
    h.cloud.reply = h.local.reply = json.dumps({"actions": [{"tool": "assign_sources", "args": args}], "reply": ""})
    c = h.engine.conversation()
    return c.execute_turn(c.understand_turn(None, said)).outcomes


def window(h, app, minutes_ago=30):
    t = h.clock.now() - timedelta(minutes=minutes_ago)
    return Observation("aw", t, "window", f"w:{app}:{minutes_ago}", {"app": app, "title": "untitled", "duration": 600},
                       None, None, Sensitivity.PERSONAL)


def test_an_app_counts_toward_a_domain_whatever_its_case_and_undo_takes_it_back():
    h = make_harness(at(2026, 10, 22, 10))
    [obs] = h.engine.ingest([window(h, "Blender")])
    assert obs.domain is None
    [said] = say(h, {"domain": "jobs", "apps": ["blender"]}, "用 Blender 的時間算 jobs")
    assert said.text.startswith("blender 算「") and said.action_id
    assert h.store.learned_rules()["app:blender"]["domain"] == "jobs"
    [later] = h.engine.ingest([window(h, "Blender", minutes_ago=5)])
    assert later.domain == "jobs"
    assert h.engine.undo_action(said.action_id) and "app:blender" not in h.store.learned_rules()


def test_a_repo_said_to_count_moves_what_was_recorded_and_undo_restores_the_rule_before():
    h = make_harness(at(2026, 10, 22, 10))
    h.store.learn_rule("repo", "garden-app", "learning", None)
    [said] = say(h, {"domain": "jobs", "repos": ["garden-app"]})
    assert h.store.learned_rules()["repo:garden-app"]["domain"] == "jobs"
    assert h.engine.undo_action(said.action_id)
    assert h.store.learned_rules()["repo:garden-app"]["domain"] == "learning"


def test_only_a_place_named_in_the_app_can_be_given_a_domain_and_an_unknown_domain_is_asked_about():
    h = make_harness(at(2026, 10, 22, 10))
    [nope] = say(h, {"domain": "jobs", "places": ["陶藝教室"]})
    assert "還不是命名過的地點" in nope.text
    h.engine.whereabouts.write_place("陶藝教室", 25.03, 121.56, None)
    [ok] = say(h, {"domain": "jobs", "places": ["陶藝教室"]})
    assert h.engine.whereabouts.saved("陶藝教室")["domain"] == "jobs"
    assert h.engine.undo_action(ok.action_id) and h.engine.whereabouts.saved("陶藝教室")["domain"] is None
    [which] = say(h, {"domain": "nowhere", "repos": ["x"]})
    assert "哪一個領域" in which.text


def test_a_new_domain_whose_goals_wait_for_a_yes_is_one_the_model_can_name():
    from muninn.core.actions import DecidedBy

    h = make_harness(at(2026, 10, 22, 10))
    h.engine.plans_actor.create({"domain": {"id": "pottery", "title": "陶藝"}, "goals": [
        {"id": "weekly-hours", "title": "陶藝 每週時數", "metric": "hours", "target": 3, "period": "week"}]}, DecidedBy.USER)
    assert {"id": "pottery", "title": "陶藝"} in h.engine.commands().context()["domains"]
    [said] = say(h, {"domain": "pottery", "apps": ["Blender"]}, "用 Blender 的時間算陶藝")
    assert said.action_id and h.store.learned_rules()["app:Blender"]["domain"] == "pottery"


def test_undo_restores_a_repo_rule_even_when_its_domain_was_put_away_since():
    from muninn.core.actions import DecidedBy

    h = make_harness(at(2026, 10, 22, 10))
    h.engine.plans_actor.create({"domain": {"id": "pottery", "title": "陶藝"}, "goals": []}, DecidedBy.USER)
    h.store.learn_rule("repo", "garden-app", "pottery", None)
    h.engine.whereabouts.write_place("陶藝教室", 25.03, 121.56, "pottery")
    [said] = say(h, {"domain": "jobs", "repos": ["garden-app"], "places": ["陶藝教室"]})
    h.engine.plans_actor.archive("pottery")  # the domain they counted toward is put away (its data kept)
    assert h.engine.undo_action(said.action_id)
    assert h.store.learned_rules()["repo:garden-app"]["domain"] == "pottery"  # as it was; counts again if pottery comes back
    assert h.engine.whereabouts.saved("陶藝教室")["domain"] == "pottery"
