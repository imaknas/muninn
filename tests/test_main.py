"""The composition root builds and serves its main views."""

from __future__ import annotations

import json

from muninn.config import Config
from muninn.main import build


def test_build_and_home_view(tmp_path, monkeypatch):
    monkeypatch.setenv("MUNINN_PUBLIC_URL", "https://muninn.example.ts.net:8444")
    (tmp_path / "attribution.yaml").write_text("sensitive_domains: [work]\ndomains: {work: Work}\n")
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    (secrets / "google_client.json").write_text(json.dumps({"web": {"client_id": "a", "client_secret": "b"}}))
    (secrets / "microsoft_client.json").write_text(json.dumps({"client_id": "a", "client_secret": "b"}))
    assert build(Config.load(tmp_path), dry_run=True).engine.mailboxes == []  # a fresh install reads no mailbox
    (tmp_path / "config.yaml").write_text("mail_accounts: [{name: personal, kind: personal, provider: gmail}, "
                                          "{name: work, kind: work, provider: outlook}]\n")
    app = build(Config.load(tmp_path), dry_run=True)
    e = app.engine
    assert ("work", "Work") in e.domains()
    assert {b.name for b in e.mailboxes} == {"personal", "work"}
    view = e.home_view()  # not connected yet: calendar and mail are simply empty
    assert view["schedule"] == [] and view["relink"] == []
    assert e.today_view()["domains"] == []
    assert set(app.connections) == {"google", "microsoft"}
    assert "ask_project" in e.commands().tools.names  # asking a project for him is offered as built
    assert "talk_to_project" not in e.commands().tools.names  # no session runner set up: nothing would take the talk
