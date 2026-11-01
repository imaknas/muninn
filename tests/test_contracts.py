"""What Muninn hands to other systems matches its published schemas
(contracts/*.schema.json): the real CLI output, run on a real store."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from muninn.config import Config
from muninn.core.project_messages import parse
from muninn.core.timewin import iso_week
from muninn.main import build, main

CONTRACTS = Path(__file__).parent.parent / "contracts"


def schema(name: str) -> Draft202012Validator:
    s = json.loads((CONTRACTS / f"{name}.v1.schema.json").read_text())
    Draft202012Validator.check_schema(s)
    return Draft202012Validator(s)


def errors(validator: Draft202012Validator, doc) -> list[str]:
    return [f"{'/'.join(map(str, e.path))}: {e.message}" for e in validator.iter_errors(doc)]


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("MUNINN_PUBLIC_URL", "https://muninn.example.ts.net:8444")
    (tmp_path / "attribution.yaml").write_text("sensitive_domains: [work]\ndomains: {jobs: Jobs, work: Work}\nrepos: {alpha: jobs}\n")
    app = build(Config.load(tmp_path), dry_run=True)
    e = app.engine
    # every kind of handoff the delivery can see
    e.handoff_log.add(project="alpha", item="Ask the dealer", fact="Delivery in Q3", quote="Q3", effect="resolves", why="asked")
    e.handoff_log.add(project="alpha", item=None, fact="Moved to Q4", effect="relevant", why="")
    app.messages.receive([{"id": "q1", "project": "alpha", "type": "question", "text": "Plan A or B?"},
                          {"id": "t1", "project": "alpha", "type": "task", "text": "Pick tiles", "due": "2026-11-30"},
                          {"id": "t2", "project": "beta", "type": "task", "text": "Sign the form", "new_reason": "a second form"},
                          {"id": "t3", "project": "alpha", "type": "task", "text": "Call the shop"}])
    app.messages.answer("q1", "B")
    e.tasks.close("t1")
    e.tasks.close("t3", "dropped")
    e.tasks.close("t2", by="evidence", why="seen in mail")  # held: not handed out yet
    e.handoff_log.add(project="beta", item="Pick tiles", fact="Already done", effect="already_done", reply_to="t9", task="t1")
    e.tasks.add("Book the dentist", due="2026-12-01", links=["deadline:jobs/dentist"])
    # one digest in the week before this one
    week_start = datetime.now().astimezone() - timedelta(days=7)
    e.store.save_digest("s1", "alpha", week_start, {"summary": "Worked on tiles", "themes": ["home"], "domain": "jobs",
                                                     "commitments": [], "decisions": [], "friction": [], "user_turns": 3},
                        week_start)
    return tmp_path, iso_week(week_start)


def run(capsys, tmp_path, *args):
    assert main(["--data", str(tmp_path), "--dry-run", *args]) == 0
    return json.loads(capsys.readouterr().out)


def test_handoffs_output_matches_its_schema(data, capsys):
    out = run(capsys, data[0], "handoffs", "--json")
    assert errors(schema("handoffs"), out) == []
    assert {h["effect"] for h in out} >= {"resolves", "relevant", "answers", "done", "dropped", "already_done"}
    assert all(h["v"] == 1 for h in out)
    assert not any(h.get("not_before") for h in out)  # a held close stays inside until due


def test_tasks_output_matches_its_schema(data, capsys):
    out = run(capsys, data[0], "tasks", "--json", "--all")
    assert errors(schema("tasks"), out) == []
    assert {t["status"] for t in out} == {"open", "done", "dropped"}


def test_digests_output_matches_its_schema(data, capsys):
    tmp_path, week = data
    out = run(capsys, tmp_path, "digests", "--week", week, "--json")
    assert errors(schema("digests"), out) == []
    assert out["v"] == 1 and out["week"] == week and len(out["sessions"]) == 1


GOOD = [
    {"id": "m1", "project": "alpha", "type": "question", "text": "Plan A or B?"},
    {"v": 1, "id": "m2", "project": "alpha", "type": "done", "text": "Done", "handoff_id": 3},
    {"id": "m3", "project": "alpha", "type": "task", "text": "Pick tiles", "due": "2026-11-30", "context": "c", "new_reason": "r"},
    {"id": "m4", "project": "alpha", "type": "drop", "text": "Not needed", "task": "m3"},
    {"id": "m5", "project": "alpha", "type": "done", "text": "Done", "task": "m3"},
]
BAD = [
    {"id": "", "project": "alpha", "type": "question", "text": "t"},
    {"id": "x", "project": "a/b", "type": "question", "text": "t"},
    {"id": "x", "project": "alpha", "type": "shout", "text": "t"},
    {"id": "x", "project": "alpha", "type": "drop", "text": "t"},  # drop needs the task
    {"id": "x", "project": "alpha", "type": "done", "text": "t", "handoff_id": "3"},
    {"v": 2, "id": "x", "project": "alpha", "type": "done", "text": "t"},
]


@pytest.mark.parametrize("message", GOOD)
def test_a_message_the_schema_accepts_muninn_accepts(message):
    assert errors(schema("messages"), {"messages": [message]}) == []
    parse(message)


@pytest.mark.parametrize("message", BAD)
def test_a_message_the_schema_rejects_muninn_rejects(message):
    assert errors(schema("messages"), {"messages": [message]}) != []
    with pytest.raises(ValueError):
        parse(message)


def test_an_unknown_body_version_is_refused(web):
    _, client, _ = web
    r = client.post("/api/messages", json={"v": 2, "messages": []}, headers={"Authorization": "Bearer tok"})
    assert r.status_code == 400 and "version" in r.json()["error"]
    assert client.post("/api/messages", json={"v": 1, "messages": []}, headers={"Authorization": "Bearer tok"}).json()["ok"]
