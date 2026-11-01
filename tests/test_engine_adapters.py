from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import ZH, TZ, at, make_harness
from muninn.adapters.channels.webpush import VapidKeys
from muninn.adapters.plans_yaml import YamlPlanRepo
from muninn.adapters.sources.agent_activity import AgentActivitySource
from muninn.adapters.sources.agent_sessions import is_human_prompt
from muninn.adapters.sources.git import GitSource
from muninn.adapters.web.app import create_app
from muninn.adapters.web.devices import DeviceRegistry

# ── engine ──────────────────────────────────────────────────────────────────


def test_morning_is_idempotent_and_pushes_once():
    h = make_harness(at(2026, 10, 22, 8, 30))
    first = h.engine.morning()
    assert len(first) == 1 and first[0].kind == "brief"
    assert "Acme Staff Engineer" in first[0].body  # deadline target inside lead window
    assert h.engine.morning() == []
    [[push]] = h.channel.sent
    assert push.text.startswith("晨報 · 先做:Acme Staff Engineer")


def test_brief_orders_domains_by_priority():
    h = make_harness(at(2026, 10, 22, 8, 30))
    body = h.engine.morning()[0].body
    assert body.index("【Job search】") < body.index("【Health】")


def test_evening_uses_wording_and_lists_digest_alerts():
    h = make_harness(at(2026, 10, 22, 22, 30))
    item = h.engine.evening()[0]
    assert item.kind == "summary" and "Cloud wording." in item.body
    assert h.engine.evening() == []


def test_push_names_the_thing_but_not_sensitive_items():
    from muninn.core.composer import Composer, ComposerConfig
    from muninn.core.models import Sensitivity, Signal

    h = make_harness(at(2026, 10, 22, 10))
    h.engine.evaluate()
    texts = [m.text for batch in h.channel.sent for m in batch]
    assert texts and all("new reminder" not in t for t in texts)
    assert any("(還有" in t for t in texts)  # several jobs alerts: the first, plus a count

    c = Composer(h.store, ComposerConfig(tz=TZ), words=ZH)
    work = Signal("w", "work", "t", 1, 1, "客戶 X 的 PR 卡住", sensitivity=Sensitivity.SENSITIVE, domain_priority=1)
    [p] = c.compose_alerts([work], at(2026, 10, 22, 11)).pushes
    assert p.text == "work: 1 new reminder"
    counts_only = Composer(h.store, ComposerConfig(tz=TZ, push_titles=False), words=ZH)
    [p] = counts_only.compose_alerts([Signal("j", "jobs", "t", 1, 1, "每週投遞落後", domain_priority=1)], at(2026, 10, 22, 12)).pushes
    assert p.text == "jobs: 1 new reminder"


def test_attribution_question_then_learned_rule():
    h = make_harness(at(2026, 10, 20, 10))
    from muninn.core.models import Observation, Sensitivity

    h.engine.ingest([Observation("git", at(2026, 10, 20, 9), "commit", "g1", {"repo": "side-thing"}, sensitivity=Sensitivity.PERSONAL)], evaluate=False)
    assert h.engine.suggest_attributions() == 1
    assert h.engine.suggest_attributions() == 0  # asked once
    c = h.store.confirmations()[0]
    assert c.options[0] == "jobs"  # local model's suggestion first
    h.engine.answer(c.id, "jobs")
    assert h.store.unattributed(10) == []
    [o] = h.store.observations(at(2026, 10, 20, 0), domain="jobs")
    assert o.sensitivity == Sensitivity.SENSITIVE  # once marked sensitive, never lowered after the fact


def test_status_change_and_undo():
    h = make_harness(at(2026, 10, 22, 10))
    change = h.engine.set_status("jobs", "acme", "applied")
    assert h.plans.plans["jobs"].tracker_item("acme").applied == date(2026, 10, 22)
    assert h.engine.undo_status(change)
    row = h.plans.plans["jobs"].tracker_item("acme")
    assert row.status == "todo" and row.applied is None


def test_hours_correction_counts_toward_goal():
    h = make_harness(at(2026, 10, 22, 10))
    h.engine.correct_hours("jobs/golang", 1.5, None)
    goals = {g["id"]: g for g in h.engine.today_view()["domains"][0]["goals"]}
    assert goals["golang"]["actual"] == 1.5


# ── YAML plan repo ──────────────────────────────────────────────────────────

PLAN_YAML = """\
domain: {id: jobs, title: Job search, priority: 1, sensitivity_default: personal}
# a human comment that must survive writes
timezone: Asia/Taipei
goals:
- id: apply
  title: Weekly applications
  metric: applications_submitted
  target: 4
  period: week
targets:
- id: acme
  company: Acme
  role: Staff Engineer
"""
TRACKER_YAML = """\
# tracker comment
- target: acme
  status: todo   # inline comment
  next_action: Apply
  next_action_due: 2026-10-24
"""


def test_yaml_repo_round_trip_keeps_comments(tmp_path):
    d = tmp_path / "domains" / "jobs"
    d.mkdir(parents=True)
    (d / "plan.yaml").write_text(PLAN_YAML)
    (d / "applications.yaml").write_text(TRACKER_YAML)
    repo = YamlPlanRepo(tmp_path / "domains", TZ)
    [plan] = repo.load()
    assert plan.priority == 1 and plan.tracker[0].next_action_due == date(2026, 10, 24)
    before = repo.set_tracker_status("jobs", "acme", "applied", date(2026, 10, 22))
    assert before == {"status": "todo", "applied": None}
    text = (d / "applications.yaml").read_text()
    assert "# tracker comment" in text and "# inline comment" in text and "applied: 2026-10-22" in text
    repo.set_tracker_status("jobs", "acme", "todo", None, replace_applied=True)
    assert "applied:" not in (d / "applications.yaml").read_text()


def test_broken_plan_is_skipped(tmp_path):
    (tmp_path / "ok").mkdir()
    (tmp_path / "ok" / "plan.yaml").write_text(PLAN_YAML)
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "plan.yaml").write_text("goals: [{id: x}]\n")  # no domain.id
    assert [p.id for p in YamlPlanRepo(tmp_path, TZ).load()] == ["jobs"]


# ── sources ─────────────────────────────────────────────────────────────────


def _line(**kw):
    return json.dumps(kw) + "\n"


def test_claude_source_counts_only_attended_activity(tmp_path):
    proj = tmp_path / "projects" / "p"
    proj.mkdir(parents=True)
    ws = tmp_path / "workspace"
    (ws / "resume").mkdir(parents=True)
    cwd = str(ws / "resume" / "sub")
    t0 = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)
    iso = lambda t: t.isoformat().replace("+00:00", "Z")  # noqa: E731
    secret = "MESSAGE-TEXT-SHOULD-NOT-APPEAR"
    lines = [
        # an agent session with no human prompt: ignored
        _line(type="user", turnOrigin="sdk", timestamp=iso(t0), cwd=cwd, sessionId="agent", message={"content": secret}),
        _line(type="assistant", timestamp=iso(t0 + timedelta(minutes=1)), cwd=cwd, sessionId="agent", message={"content": secret}),
        # a human prompt and the reply that follows
        _line(type="user", turnOrigin="human", timestamp=iso(t0), cwd=cwd, sessionId="s1", message={"content": secret}),
        _line(type="assistant", timestamp=iso(t0 + timedelta(minutes=2)), cwd=cwd, sessionId="s1", message={"content": secret}),
        # long after the human left: ignored
        _line(type="assistant", timestamp=iso(t0 + timedelta(minutes=40)), cwd=cwd, sessionId="s1", message={"content": secret}),
    ]
    (proj / "s.jsonl").write_text("".join(lines))
    src = AgentActivitySource([tmp_path / "projects"], [ws])
    found, cursor = src.collect(None, t0 + timedelta(hours=1))
    assert [o.payload["repo"] for o in found] == ["resume"]
    assert found[0].payload["human_turns"] == 1 and found[0].payload["events"] == 2
    assert secret not in json.dumps([o.payload for o in found])
    again, _ = src.collect(cursor, t0 + timedelta(hours=1))
    assert again == []


def test_human_prompt_detection():
    assert is_human_prompt({"type": "user", "turnOrigin": "human"})
    assert not is_human_prompt({"type": "user", "turnOrigin": "peer"})
    assert not is_human_prompt({"type": "user", "toolUseResult": {}, "turnOrigin": None})
    assert is_human_prompt({"type": "user", "entrypoint": "cli"})


def _git(repo: Path, *args, env=None):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def test_git_source_metadata_only(tmp_path):
    import os

    repo = tmp_path / "ws" / "go-katas"
    repo.mkdir(parents=True)
    env = {**os.environ, "GIT_AUTHOR_EMAIL": "me@example.com", "GIT_COMMITTER_EMAIL": "me@example.com", "GIT_AUTHOR_NAME": "me", "GIT_COMMITTER_NAME": "me"}
    _git(repo, "init", "-q", env=env)
    (repo / "main.go").write_text("package main\n")
    _git(repo, "add", ".", env=env)
    _git(repo, "commit", "-q", "-m", "SECRET-SUBJECT", env=env)
    (repo / "util.py").write_text("x = 1\n")
    _git(repo, "add", ".", env=env)
    _git(repo, "commit", "-q", "-m", "wip\n\nCo-Authored-By: Claude <noreply@anthropic.com>", env=env)
    src = GitSource([tmp_path / "ws"], ["me@example.com"])
    found, cursor = src.collect(None, datetime.now(timezone.utc))
    kinds = sorted((o.kind, tuple(o.payload["langs"])) for o in found)
    assert kinds == [("agent_commit", ("python",)), ("commit", ("go",))]
    assert "SECRET-SUBJECT" not in json.dumps([o.payload for o in found])
    assert GitSource([tmp_path / "ws"], ["other@example.com"]).collect(None, datetime.now(timezone.utc))[0] == []


def test_a_worktree_beside_its_repo_is_not_another_project(tmp_path):
    import os

    repo = tmp_path / "ws" / "garden"
    repo.mkdir(parents=True)
    env = {**os.environ, "GIT_AUTHOR_EMAIL": "me@example.com", "GIT_COMMITTER_EMAIL": "me@example.com", "GIT_AUTHOR_NAME": "me", "GIT_COMMITTER_NAME": "me"}
    _git(repo, "init", "-q", env=env)
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", ".", env=env)
    _git(repo, "commit", "-q", "-m", "one", env=env)
    _git(repo, "worktree", "add", "-q", "-b", "fix", str(tmp_path / "ws" / "garden-fix"), env=env)
    found, cursor = GitSource([tmp_path / "ws"], ["me@example.com"]).collect(None, datetime.now(timezone.utc))
    assert {o.payload["repo"] for o in found} == {"garden"} and set(cursor) == {"garden"}


# ── web ─────────────────────────────────────────────────────────────────────


def test_api_requires_paired_device(web):
    h, client, devices = web
    assert client.get("/healthz").status_code == 200
    assert client.get("/").status_code == 200  # the shell carries no content
    assert client.get("/privacy").status_code == 200
    assert client.get("/static/style.css").headers["cache-control"] == "no-cache"  # the compiled .js is not in the repo
    assert client.get("/api/today").status_code == 401
    assert client.post("/api/pair", json={"code": "WRONG-CODE"}).status_code == 403
    code = devices.new_pairing_code()
    r = client.post("/api/pair", json={"code": code.lower(), "name": "iPhone"})
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/api/today").status_code == 200
    assert client.post("/api/pair", json={"code": code}).status_code == 403  # single use


def test_pairing_code_dies_after_failed_attempts(web):
    _, client, devices = web
    code = devices.new_pairing_code()
    for _ in range(5):
        client.post("/api/pair", json={"code": "AAAA-AAAA"})
    assert client.post("/api/pair", json={"code": code}).status_code == 403


def test_revoked_device_is_locked_out(web):
    _, client, devices = web
    client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "x"})
    devices.revoke(devices.devices()[0]["id"])
    assert client.get("/api/today").status_code == 401


def test_post_requires_json(web):
    _, client, devices = web
    client.post("/api/pair", json={"code": devices.new_pairing_code()})
    r = client.post("/api/input/note", content="text=hi", headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 400


def test_ingest_needs_token_and_reevaluates(web):
    h, client, _ = web
    body = {"observations": [{"source": "aw", "key": "1", "ts": "2026-10-22T09:00:00+08:00", "kind": "window",
                              "payload": {"app": "X", "title": "", "duration_s": 60}}]}
    assert client.post("/api/ingest", json=body).status_code == 401
    r = client.post("/api/ingest", json=body, headers={"authorization": "Bearer tok"})
    assert r.json() == {"ok": True, "new": 1, "refused": []}
    [o] = h.store.observations(at(2026, 10, 22, 0))
    assert o.sensitivity.name == "SENSITIVE"  # external default, unattributed


def test_tailscale_login_allowlist(tmp_path):
    h = make_harness(at(2026, 10, 22, 10))
    keys = VapidKeys(tmp_path / "v.pem")
    keys.ensure()
    devices = DeviceRegistry(tmp_path / "d.db")
    client = TestClient(create_app(h.engine, keys, "tok", devices, allowed_logins=["me@example.com"]), base_url="https://testserver")
    code = devices.new_pairing_code()
    assert client.post("/api/pair", json={"code": code}).status_code == 403
    assert client.post("/api/pair", json={"code": code}, headers={"Tailscale-User-Login": "me@example.com"}).status_code == 200
    assert client.get("/api/today").status_code == 403
    assert client.get("/api/today", headers={"Tailscale-User-Login": "me@example.com"}).status_code == 200


def test_vapid_key_is_uncompressed_p256(tmp_path):
    import base64

    keys = VapidKeys(tmp_path / "k.pem")
    assert keys.ensure() and not keys.ensure()
    raw = base64.urlsafe_b64decode(keys.application_server_key() + "==")
    assert len(raw) == 65 and raw[0] == 4
    assert oct(keys.path.stat().st_mode & 0o777) == "0o600"


def test_rules_added_later_resolve_backlog_and_questions():
    from dataclasses import replace

    from muninn.core.models import Observation, Sensitivity
    from muninn.core.plan import parse_plan

    h = make_harness(at(2026, 10, 20, 10))
    h.engine.ingest([Observation("git", at(2026, 10, 20, 9), "commit", "g1", {"repo": "thesis"}, sensitivity=Sensitivity.PERSONAL)], evaluate=False)
    h.engine.suggest_attributions()
    [q] = h.engine.pending_confirmations()
    assert "phd" not in q.options
    # a new domain plan appears: the open question offers it without being re-asked
    h.plans.plans["phd"] = parse_plan({"domain": {"id": "phd", "title": "PhD", "priority": 3}, "timezone": TZ, "goals": []}, (), TZ)
    assert "phd" in h.engine.pending_confirmations()[0].options
    # a rule for the repo arrives in attribution.yaml: backlog gets a domain, the question goes away
    cfg = h.engine.attr_cfg
    h.engine.attr_cfg = replace(cfg, rules={**cfg.rules, "repos": {**cfg.rules["repos"], "thesis": "phd"}})
    assert h.engine.reattribute_unattributed() == 1
    assert h.engine.pending_confirmations() == []
    assert h.engine.answer(q.id, "phd") is None  # no longer open


def test_changing_a_repo_answer_moves_its_history():
    from muninn.core.models import Observation, Sensitivity

    h = make_harness(at(2026, 10, 20, 10))
    h.engine.ingest([Observation("git", at(2026, 10, 20, 9), "commit", "g1", {"repo": "side-thing"}, sensitivity=Sensitivity.PERSONAL)], evaluate=False)
    h.engine.suggest_attributions()
    [q] = h.engine.pending_confirmations()
    h.engine.answer(q.id, "jobs")
    assert h.engine.attribution_decisions() == [{"repo": "side-thing", "domain": "jobs"}]
    # chose wrong: move it to health
    assert h.engine.set_repo_domain("side-thing", "health") == 1
    assert [o.domain for o in h.store.observations_for_repo("side-thing")] == ["health"]
    # and to "none of these": back to unattributed and sensitive
    h.engine.set_repo_domain("side-thing", None)
    [o] = h.store.observations_for_repo("side-thing")
    assert o.domain is None and o.sensitivity == Sensitivity.SENSITIVE
    assert h.engine.attribution_decisions() == [{"repo": "side-thing", "domain": None}]
    with pytest.raises(ValueError):
        h.engine.set_repo_domain("side-thing", "nope")


def test_attribution_api(web):
    h, client, devices = web
    client.post("/api/pair", json={"code": devices.new_pairing_code()})
    assert client.get("/api/attribution").json() == {"items": []}
    r = client.post("/api/attribution", json={"repo": "x", "domain": "jobs"})
    assert r.json() == {"ok": True, "reattributed": 0}


def test_home_directory_is_not_a_project(tmp_path):
    from pathlib import Path

    from muninn.adapters.sources.agent_activity import repo_name

    assert repo_name("/Users/someone", []) is None
    assert repo_name(str(Path.home()), []) is None
    assert repo_name("/home/someone/code/resume/sub", [Path("/home/someone/code")]) == "resume"


def test_forget_repo():
    from muninn.core.models import Observation, Sensitivity

    h = make_harness(at(2026, 10, 20, 10))
    h.engine.ingest([Observation("claude_code", at(2026, 10, 20, 9), "claude_activity", "c1", {"repo": "moe"}, sensitivity=Sensitivity.PERSONAL)], evaluate=False)
    h.engine.suggest_attributions()
    assert h.engine.pending_confirmations()
    assert h.engine.forget_repo("moe") == 1
    assert h.engine.pending_confirmations() == [] and h.engine.attribution_decisions() == []
    assert "repo" not in h.store.observations(at(2026, 10, 20, 0))[0].payload


def test_moving_a_repo_moves_windows_naming_it():
    from muninn.core.models import Observation, Sensitivity

    h = make_harness(at(2026, 10, 21, 20))
    h.engine.ingest([Observation("aw", at(2026, 10, 21, 10), "window", "w1", {"app": "Chrome", "title": "go-katas – Vercel", "duration_s": 60},
                                 sensitivity=Sensitivity.PERSONAL)], evaluate=False)
    assert h.store.observations(at(2026, 10, 21, 0), kinds=("window",))[0].goal == "jobs/golang"
    h.engine.set_repo_domain("go-katas", "health")
    [o] = h.store.observations(at(2026, 10, 21, 0), kinds=("window",))
    assert (o.domain, o.goal) == ("health", None)  # the user's answer beats the config, goal included



def test_a_web_app_installed_without_its_build_says_how_to_build_it(web, monkeypatch):
    _, client, _ = web
    monkeypatch.setattr("muninn.adapters.web.app.unbuilt", lambda: ["app.js", "sw.js"])
    r = client.get("/")
    assert r.status_code == 503 and "npm run build" in r.text and "muninn-ai" in r.text
    assert client.get("/sw.js").status_code == 404
