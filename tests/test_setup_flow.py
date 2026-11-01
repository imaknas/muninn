"""First-run setup from the browser: who may reach it, where the key goes, what config becomes, the phone's QR."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from conftest import at, make_harness
from muninn.adapters.channels.webpush import VapidKeys
from muninn.adapters.web.app import create_app
from muninn.adapters.web.devices import DeviceRegistry
from muninn.config import Config
from muninn.core.ports import CloudLoginLost
from muninn.main import IMPLS
from muninn.setup_flow import Setup

KEY = "sk-ant-invented-setup-key-000000000000"
TOKEN = "invented-setup-token"


class Listed(Setup):
    rejected = False
    asked: list = []
    silent = None  # a model that does not answer

    def _answers(self, name, spec, model, words):
        Listed.asked = [*Listed.asked, model]
        if model == self.silent:
            raise ValueError(words("setup_flow.no_answer", model=model, why="(empty)"))

    def models(self, choice):
        if self.rejected:
            raise CloudLoginLost("rejected")
        return super().models(choice) if choice.get("impl") == "none" else self._listed(choice)

    def _listed(self, choice):
        name, spec = self._spec(choice)
        if key := str(choice.get("key") or "").strip():
            self._write_key(name, key)
        return ["claude-haiku-5-5", "claude-sonnet-5-5"]


@pytest.fixture
def web(tmp_path):
    h = make_harness(at(2026, 11, 3, 9))
    keys = VapidKeys(tmp_path / "vapid.pem")
    keys.ensure()
    devices = DeviceRegistry(tmp_path / "dev.db")
    (tmp_path / "config.yaml").write_text("# my own settings\nowner_label: Robin  # how notes name me\n", encoding="utf-8")
    restarts = []
    setup = Listed(Config.load(tmp_path), IMPLS, restart=lambda: restarts.append(1) or True, token=TOKEN)
    client = TestClient(create_app(h.engine, keys, "tok", devices, setup=setup, public_url="https://muninn.example.ts.net"),
                        base_url="https://testserver")
    return client, devices, setup, tmp_path, restarts


def test_before_any_device_is_paired_only_the_printed_token_opens_setup_then_only_a_paired_device(web):
    client, devices, *_ = web
    assert client.get("/api/setup").status_code == 403  # through tailscale serve, every request is from here
    assert client.get("/api/setup", headers={"x-setup-token": "guess"}).status_code == 403
    assert client.get("/api/setup", headers={"x-setup-token": TOKEN}).status_code == 200
    client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "phone"})
    other = TestClient(client.app, base_url="https://testserver")
    assert other.get("/api/setup", headers={"x-setup-token": TOKEN}).status_code == 401  # the token is done with
    assert client.get("/api/setup").status_code == 200  # the paired phone


def test_the_key_goes_to_its_own_file_and_nowhere_else(web, caplog):
    client, _, setup, tmp, _ = web
    r = client.post("/api/setup/models", json={"impl": "anthropic", "key": KEY}, headers={"x-setup-token": TOKEN})
    assert r.json() == {"models": ["claude-haiku-5-5", "claude-sonnet-5-5"]} and KEY not in r.text
    key_file = tmp / "secrets" / "anthropic-api-key"
    assert key_file.read_text().strip() == KEY and oct(key_file.stat().st_mode)[-3:] == "600"
    assert KEY not in caplog.text
    setup.rejected = True
    r = client.post("/api/setup/models", json={"impl": "anthropic", "key": KEY}, headers={"x-setup-token": TOKEN})
    assert r.status_code == 400 and "被拒絕" in r.json()["error"]


def test_saving_writes_the_provider_where_the_key_is_and_keeps_the_rest_of_config(web):
    client, _, _, tmp, restarts = web
    r = client.post("/api/setup/save", json={"impl": "anthropic", "fast": "claude-haiku-5-5", "standard": "claude-sonnet-5-5"},
                    headers={"x-setup-token": TOKEN})
    assert r.json() == {"ok": True, "restarting": True} and restarts == [1]
    text = (tmp / "config.yaml").read_text()
    assert "# my own settings" in text and "# how notes name me" in text and "owner_label: Robin" in text
    llm = Config.load(tmp)["llm"]
    assert llm["providers"]["anthropic"] == {"impl": "anthropic", "key": {"secret": "anthropic-api-key"},
                                             "models": {"fast": "claude-haiku-5-5", "standard": "claude-sonnet-5-5"}}
    assert llm["use"] == {"fast": "anthropic", "standard": "anthropic"} and llm["routing"] == "cloud"
    assert oct((tmp / "config.yaml").stat().st_mode)[-3:] == "600"


def test_a_local_model_saved_is_the_local_one_and_routing_stays_private(web):
    client, _, _, tmp, _ = web
    client.post("/api/setup/save", json={"impl": "ollama", "fast": "qwen3:8b"}, headers={"x-setup-token": TOKEN})
    llm = Config.load(tmp)["llm"]
    assert llm["providers"]["ollama"]["models"] == {"fast": "qwen3:8b"} and "use" not in llm and llm["routing"] == "privacy"


def test_the_phone_is_paired_by_scanning_a_code_for_the_https_address(web):
    client, devices, *_ = web
    r = client.post("/api/setup/pair", json={}, headers={"x-setup-token": TOKEN}).json()
    assert r["url"] == f"https://muninn.example.ts.net/?pair={r['code']}" and r["qr"].startswith("<svg")
    assert devices.pair(r["code"], "phone") is not None  # a real pairing code


def test_the_language_chosen_in_setup_is_written_with_the_model_and_the_settings_page_changes_it(web):
    client, devices, _, tmp, restarts = web
    r = client.post("/api/setup/save", json={"impl": "ollama", "fast": "qwen", "language": "en"}, headers={"x-setup-token": TOKEN})
    assert r.json() == {"ok": True, "restarting": True}
    assert "language: en" in (tmp / "config.yaml").read_text() and Config.load(tmp).language == "en"
    client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "phone"})
    assert client.post("/api/settings/language", json={"language": "zh-TW"}).json() == {"ok": True, "restarting": True}
    text = (tmp / "config.yaml").read_text()
    assert "language: zh-TW" in text and "owner_label: Robin  # how notes name me" in text and len(restarts) == 2
    r = client.post("/api/settings/language", json={"language": "fr"})
    assert r.status_code == 400 and "zh-TW, en" in r.json()["error"] and Config.load(tmp).language == "zh-TW"


def test_the_setup_link_outlives_the_restart_saving_the_model_makes_until_a_phone_is_paired(tmp_path):
    from muninn.main import first_run_token

    (tmp_path / "config.yaml").write_text("language: zh-TW\n")
    devices = DeviceRegistry(tmp_path / "dev.db")
    first = first_run_token(Config.load(tmp_path), devices)
    again = first_run_token(Config.load(tmp_path), devices)  # Muninn started again (the settings were saved)
    assert first and again == first and (tmp_path / "setup-url").read_text().strip().endswith(f"#setup={first}")
    devices.pair(devices.new_pairing_code(), "phone")  # a phone paired: the link is done with
    assert first_run_token(Config.load(tmp_path), devices) is None and not (tmp_path / "setup-url").exists()



def test_a_model_that_does_not_answer_is_refused_before_anything_is_written(web):
    client, _, setup, tmp_path, restarts = web
    before = (tmp_path / "config.yaml").read_text()
    setup.silent = "bge-m3:latest"
    r = client.post("/api/setup/save", headers={"X-Setup-Token": TOKEN},
                    json={"impl": "ollama", "fast": "bge-m3:latest", "language": "en"})
    assert r.status_code == 400 and "did not answer a plain question" in r.json()["error"]
    assert (tmp_path / "config.yaml").read_text() == before and restarts == []


def test_ollama_lists_only_the_models_that_hold_a_conversation(monkeypatch):
    import httpx

    from muninn.adapters.llm.ollama import OllamaProvider

    tags = {"models": [{"name": "bge-m3:latest"}, {"name": "qwen2.5:7b"}, {"name": "old:1b"}]}
    caps = {"bge-m3:latest": ["embedding"], "qwen2.5:7b": ["completion", "tools"], "old:1b": None}

    class R:
        def __init__(self, data):
            self.status_code, self._data = 200, data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    monkeypatch.setattr(httpx, "get", lambda url, timeout: R(tags))
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: R({"capabilities": caps[json["model"]]}))
    provider = OllamaProvider.__new__(OllamaProvider)
    provider._url = "http://127.0.0.1:11434"
    assert provider.models() == ["old:1b", "qwen2.5:7b"]  # an Ollama too old to say is listed


def test_only_a_browser_on_this_computer_pairs_without_https_and_only_its_cookie_is_not_secure(web):
    client, devices, _, _, _ = web
    app = client.app

    def asking(host: str, peer: str) -> TestClient:
        return TestClient(app, base_url=f"http://{host}", client=(peer, 50000))

    spoofed = asking("127.0.0.1:18795", "100.64.0.9")  # says it asks for this computer, but connects from elsewhere
    assert spoofed.post("/api/setup/pair-here", headers={"X-Setup-Token": TOKEN}, json={}).status_code == 403
    here = asking("127.0.0.1:18795", "127.0.0.1")
    code = here.post("/api/setup/pair-here", headers={"X-Setup-Token": TOKEN}, json={}).json()["code"]
    r = here.post("/api/pair", json={"code": code, "name": "This computer"})
    assert r.status_code == 200 and "secure" not in r.headers["set-cookie"].lower()
    proxied = asking("muninn.example.ts.net:8444", "127.0.0.1")  # tailscale serve: from here, for the tailnet name
    r = proxied.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "phone"})
    assert r.status_code == 200 and "secure" in r.headers["set-cookie"].lower()


def test_a_phone_is_paired_from_the_settings_page_once_there_is_an_https_address(web):
    client, devices, _, _, _ = web
    client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "laptop"})
    p = client.post("/api/devices/pair", json={}).json()
    assert p["url"].startswith("https://muninn.example.ts.net/?pair=") and p["code"] and p["qr"].startswith("<svg")
