"""The Mac collector (src/muninn/collectors/mac) and the server pieces it relies on."""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

from conftest import at, make_harness
from muninn.core.attribution import Attributor, rules_from_config
from muninn.core.metrics import estimate_hours
from muninn.core.models import Observation, Sensitivity

spec = importlib.util.spec_from_file_location("muninn_agent", Path(__file__).parents[1] / "src" / "muninn" / "collectors" / "mac" / "muninn_agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def ev(ts: str, dur: float, app: str, title: str = "t") -> dict:
    return {"timestamp": ts, "duration": dur, "data": {"app": app, "title": title}}


def test_chrome_windows_overlapping_the_work_extension_are_work():
    work = [(datetime(2026, 10, 21, 1, 0, tzinfo=timezone.utc), datetime(2026, 10, 21, 1, 30, tzinfo=timezone.utc))]
    obs = agent.window_observations([
        ev("2026-10-21T01:10:00+00:00", 600, "Google Chrome", "Slack | eng"),
        ev("2026-10-21T02:00:00+00:00", 600, "Google Chrome", "arXiv"),
        ev("2026-10-21T02:20:00+00:00", 900, "Code", "main.go — acme-api"),
        ev("2026-10-21T02:40:00+00:00", 2, "Finder", "flicker"),
    ], work, {"Google Chrome"})
    assert [o["payload"].get("browser_profile") for o in obs] == ["work", "personal", None]
    assert obs[0]["payload"]["duration_s"] == 600 and obs[0]["kind"] == "window"


def test_repo_names_match_the_server(tmp_path):
    assert agent.repo_name(Path.home() / "acme" / "api", ["~"]) == "acme"
    assert agent.repo_name(Path.home() / "resume", ["~"]) == "resume"
    (tmp_path / "acme" / "api" / ".git").mkdir(parents=True)
    (tmp_path / "acme" / "billing" / "coordinator" / ".git").mkdir(parents=True)
    (tmp_path / "resume" / ".git").mkdir(parents=True)
    (tmp_path / "notes" / "deep" / ".git").mkdir(parents=True)  # not a nested root: ignored
    (tmp_path / "resume-fix").mkdir()
    (tmp_path / "resume-fix" / ".git").write_text(f"gitdir: {tmp_path}/resume/.git/worktrees/resume-fix\n")  # its commits are resume's
    found = agent.repos([str(tmp_path)], {"acme"})
    assert sorted(p.name for p in found) == ["api", "coordinator", "resume"]


def test_durations_count_toward_hours():
    t = at(2026, 10, 21, 10)
    spans = [(t, t + timedelta(hours=2)), (t + timedelta(hours=2, minutes=10), t + timedelta(hours=2, minutes=40))]
    # joined (gap 10 min ≤ 20): 2h40m + 10 min pad
    assert abs(estimate_hours(spans, timedelta(minutes=20), timedelta(minutes=10)) - (2 + 50 / 60)) < 1e-9


def test_work_profile_rule_and_ingest_attribution():
    rules = rules_from_config({"fields": [{"field": "browser_profile", "equals": "work", "domain": "work"}],
                               "windows": [{"title": "(?i)arxiv", "domain": "jobs"}]})
    attr = Attributor(rules, {"jobs": Sensitivity.PERSONAL}, ["work"])
    o = attr.attribute(Observation("aw", at(2026, 10, 21, 10), "window", "k", {"app": "Google Chrome", "title": "arXiv", "browser_profile": "work"}, sensitivity=Sensitivity.PERSONAL))
    assert o.domain == "work" and o.sensitivity == Sensitivity.SENSITIVE


def test_window_hours_reach_the_goal_through_ingest():
    h = make_harness(at(2026, 10, 21, 20))
    from dataclasses import replace

    h.engine.attr_cfg = replace(h.engine.attr_cfg, rules={**h.engine.attr_cfg.rules, "windows": [{"title": "go-katas", "domain": "jobs", "goal": "golang"}]})
    h.engine.ingest([Observation("aw", at(2026, 10, 21, 18), "window", "w1", {"app": "Code", "title": "main.go — go-katas", "duration_s": 3600}, sensitivity=Sensitivity.PERSONAL)])
    goals = {g["id"]: g for g in h.engine.today_view()["domains"][0]["goals"]}
    assert abs(goals["golang"]["actual"] - (1 + 10 / 60)) < 0.01


def test_transcripts_still_sync_when_muninn_cannot_be_reached(tmp_path, monkeypatch):
    (tmp_path / "config.json").write_text('{"ingest_url": "https://muninn.example/api/ingest", "token": "t"}')
    monkeypatch.setattr(agent, "CONFIG", tmp_path / "config.json")
    monkeypatch.setattr(agent, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(agent, "collect_windows", lambda cfg, state, now: [{"key": "w1"}])
    monkeypatch.setattr(agent, "collect_git", lambda cfg, state, now: [])

    def unreachable(cfg, observations):
        raise agent.urllib.error.URLError("tlsv1 alert internal error")

    synced = []
    monkeypatch.setattr(agent, "ingest", unreachable)
    monkeypatch.setattr(agent, "sync_transcripts", lambda cfg: synced.append(cfg))
    assert agent.main() == 1
    assert len(synced) == 1


def test_a_computer_that_went_quiet_is_said_once_per_silence():
    h = make_harness(at(2026, 10, 21, 20))
    assert h.engine.remind_quiet_computer() == []  # no computer ever sent anything: nothing to miss
    h.engine.ingest([Observation("aw", at(2026, 10, 21, 18), "window", "w1", {"app": "Code", "title": "notes"},
                                 sensitivity=Sensitivity.PERSONAL)])
    h.clock.t = at(2026, 10, 22, 20)
    assert h.engine.remind_quiet_computer() == []  # a day off the computer is not a silence
    h.clock.t = at(2026, 10, 24, 9)
    [item] = h.engine.remind_quiet_computer()
    assert "2" in item.title and "~/.muninn-agent/agent.log" in item.body
    h.clock.t = at(2026, 10, 25, 9)
    assert h.engine.remind_quiet_computer() == []  # once, not every tick
    h.engine.ingest([Observation("aw", at(2026, 10, 25, 8), "window", "w2", {"app": "Code", "title": "notes"},
                                 sensitivity=Sensitivity.PERSONAL)])
    h.clock.t = at(2026, 10, 28, 9)
    assert len(h.engine.remind_quiet_computer()) == 1  # a new silence after it came back is said again
