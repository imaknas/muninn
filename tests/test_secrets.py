"""Where Muninn's keys live is set, never assumed; one that is missing fails and is said,
never quietly made again or done without."""

from __future__ import annotations

import json
import stat

import httpx
import pytest

from conftest import at, make_harness
from muninn.adapters.channels.webpush import VapidKeys
from muninn.config import DEFAULTS, Config, MissingSecret


def test_the_keys_live_under_the_data_directory_unless_said_otherwise(tmp_path, monkeypatch):
    monkeypatch.delenv("MUNINN_SECRETS_DIR", raising=False)
    assert Config.load(tmp_path).secrets_dir == tmp_path / "secrets"
    (tmp_path / "config.yaml").write_text("secrets_dir: keys\n")
    assert Config.load(tmp_path).secrets_dir == tmp_path / "keys"  # relative to the data directory
    monkeypatch.setenv("MUNINN_SECRETS_DIR", str(tmp_path / "elsewhere"))
    assert Config.load(tmp_path).secrets_dir == tmp_path / "elsewhere"
    assert DEFAULTS["llm"]["claude_token_file"] is None  # no path in the code: the CLI's own login unless config names one


def test_only_init_makes_the_ingest_token(tmp_path, monkeypatch):
    monkeypatch.setenv("MUNINN_SECRETS_DIR", str(tmp_path / "secrets"))
    cfg = Config.load(tmp_path)
    with pytest.raises(MissingSecret, match="ingest_token"):
        cfg.ingest_token()
    token = cfg.ingest_token(create=True)
    assert cfg.ingest_token() == token
    assert stat.S_IMODE((cfg.secrets_dir / "ingest_token").stat().st_mode) == 0o600


def test_serve_refuses_to_start_without_its_keys_rather_than_making_new_ones(tmp_path, monkeypatch, caplog):
    from muninn import main

    monkeypatch.setenv("MUNINN_SECRETS_DIR", str(tmp_path / "secrets"))
    (tmp_path / "attribution.yaml").write_text("sensitive_domains: [work]\n")
    assert main.main(["--data", str(tmp_path), "serve"]) == 2
    assert "vapid_private.pem" in caplog.text and not (tmp_path / "secrets" / "vapid_private.pem").exists()
    assert main.main(["--data", str(tmp_path), "init"]) == 0
    cfg = Config.load(tmp_path)
    assert main.require_secrets(cfg, VapidKeys(cfg.secrets_dir / "vapid_private.pem")) == "push key and ingest token present"


def google(tmp_path, transport=None):
    from muninn.adapters.google.oauth import GoogleOAuth

    (tmp_path / "client.json").write_text(json.dumps({"web": {"client_id": "cid", "client_secret": "cs"}}))
    return GoogleOAuth(tmp_path / "client.json", tmp_path / "tok.json", tmp_path / "state.json", "https://x/cb",
                       http=httpx.Client(transport=transport or httpx.MockTransport(lambda r: httpx.Response(500))))


def test_a_google_token_that_disappears_asks_to_relink_and_pushes(tmp_path):
    g = google(tmp_path)
    assert g.status() == "off"  # never linked: nothing to say
    (tmp_path / "tok.json").write_text(json.dumps({"access_token": "a", "refresh_token": "r", "expires_at": 9e9}))
    assert g.status() == "ok"  # linked (also for a connection made before this was kept)
    (tmp_path / "tok.json").unlink()  # lost, not revoked
    assert g.status() == "relink"
    h = make_harness(at(2026, 10, 22, 10))
    h.engine.connections = [g]
    assert h.engine.home_view()["relink"] == ["google"] and len(h.engine.remind_relink()) == 1 and len(h.channel.sent) == 1


def test_linking_again_clears_it(tmp_path):
    def answer(r):
        return httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600})

    g = google(tmp_path, httpx.MockTransport(answer))
    (tmp_path / "tok.json").write_text(json.dumps({"access_token": "a", "refresh_token": "r", "expires_at": 9e9}))
    g.status()
    (tmp_path / "tok.json").unlink()
    assert g.status() == "relink"
    url = g.authorization_url()
    state = dict(httpx.QueryParams(url.split("?", 1)[1]))["state"]
    g.complete("code", state)
    assert g.status() == "ok"
