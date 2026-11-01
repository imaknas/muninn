from __future__ import annotations

from conftest import at, make_harness
from starlette.testclient import TestClient

from muninn.adapters.channels.webpush import VapidKeys
from muninn.adapters.web.app import create_app
from muninn.adapters.web.devices import DeviceRegistry


def client(tmp_path, checks):
    h = make_harness(at(2026, 10, 22, 10))
    keys = VapidKeys(tmp_path / "vapid.pem")
    keys.ensure()
    app = create_app(h.engine, keys, "tok", DeviceRegistry(tmp_path / "d.db"), smoke_checks=checks)
    return TestClient(app, base_url="https://testserver")


def test_smoke_runs_every_check_and_reports_each(tmp_path):
    def broken():
        raise RuntimeError("claude CLI exited 1: env: node: No such file")

    c = client(tmp_path, [("store", lambda: "3 tasks"), ("cloud model", broken), ("local model", lambda: "reachable")])
    assert c.get("/api/smoke").status_code == 401
    r = c.get("/api/smoke", headers={"authorization": "Bearer tok"}).json()
    assert r["ok"] is False
    assert [(x["name"], x["ok"]) for x in r["checks"]] == [("store", True), ("cloud model", False), ("local model", True)]
    assert "node" in r["checks"][1]["detail"]
