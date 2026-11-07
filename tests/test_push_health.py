"""Whether pushes reach a phone: a subscription belongs to the device it came from, the settings say on only when
Muninn pushes to this device, and pushes the service took that no phone showed for hours are said."""

from __future__ import annotations

from datetime import timedelta

from starlette.testclient import TestClient

from conftest import at, make_harness
from muninn.adapters.channels.webpush import VapidKeys
from muninn.adapters.web.app import create_app
from muninn.adapters.web.devices import DeviceRegistry
from muninn.core.platform_alerts import notice

SUB = {"endpoint": "https://web.push.example.com/a", "keys": {"p256dh": "x", "auth": "y"}}


def test_a_device_has_one_subscription_and_one_from_before_devices_had_them_is_kept():
    h = make_harness(at(2026, 11, 7))
    s = h.store
    s.add_subscription({**SUB, "endpoint": "https://web.push.example.com/old"}, at(2026, 10, 19))
    s.add_subscription(SUB, at(2026, 11, 7), 3)
    s.add_subscription({**SUB, "endpoint": "https://web.push.example.com/b"}, at(2026, 11, 8), 3)
    assert sorted(x["endpoint"] for x in s.subscriptions()) == ["https://web.push.example.com/b", "https://web.push.example.com/old"]
    assert s.subscribed(3) and not s.subscribed(1)
    s.add_subscription({**SUB, "endpoint": "https://web.push.example.com/b"}, at(2026, 11, 9))  # sent again unlinked: still 3's
    assert s.subscribed(3)
    s.remove_device_subscriptions(3)
    assert not s.subscribed(3) and len(s.subscriptions()) == 1


def test_pushes_taken_but_never_shown_for_hours_are_said_once_a_day():
    h = make_harness(at(2026, 11, 7, 10))
    ph = h.engine.push_health
    ph.taken()
    h.clock.t += timedelta(hours=1)
    ph.taken()  # counted from the first unshown one
    assert ph.status()["reaching"] and ph.check() is None
    h.clock.t = at(2026, 11, 7, 17)
    assert not ph.status()["reaching"]
    item = ph.check()
    assert item.title == "手機已經 7 小時沒收到推播" and item.kind == "alert"
    assert ph.check() is None  # once a day
    ph.shown(3)
    assert ph.status() == {"reaching": True, "unshown_since": None}


def test_a_phone_off_at_night_is_not_taken_for_pushes_not_arriving():
    h = make_harness(at(2026, 11, 7, 22, 50))
    ph = h.engine.push_health
    ph.taken()  # the last push before quiet hours (23:00 to 08:00), never shown
    h.clock.t = at(2026, 11, 8, 5)
    assert ph.status()["reaching"] and ph.check() is None
    h.clock.t = at(2026, 11, 8, 13)
    assert ph.status()["reaching"]  # 10 minutes, and five hours of the morning
    h.clock.t = at(2026, 11, 8, 14)
    assert not ph.status()["reaching"]


def test_nothing_to_push_says_nothing_is_wrong_however_long_it_has_been():
    h = make_harness(at(2026, 11, 7, 10))
    h.clock.t += timedelta(days=9)
    assert h.engine.push_health.status() == {"reaching": True, "unshown_since": None}


def test_a_push_held_through_quiet_hours_is_not_counted_until_it_is_sent():
    h = make_harness(at(2026, 11, 7, 23, 30))
    h.engine.platform_alerts.receive(notice({"v": 1, "source": "brunna", "key": "k", "state": "open", "at": "2026-11-07T15:30:00Z", "text": "t"}))
    assert h.engine.push_health.status()["unshown_since"] is None


def test_the_settings_say_on_only_when_muninn_pushes_to_this_device(tmp_path):
    h = make_harness(at(2026, 11, 7, 10))
    keys = VapidKeys(tmp_path / "v.pem")
    keys.ensure()
    devices = DeviceRegistry(tmp_path / "d.db")
    client = TestClient(create_app(h.engine, keys, "tok", devices), base_url="https://testserver")
    assert client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "phone"}).status_code == 200
    me = client.get("/api/me").json()
    assert (me["push_subscribed"], me["push_reaching"]) == (False, True)
    assert client.post("/api/push/subscribe", json={"subscription": SUB}).json() == {"ok": True}
    assert client.get("/api/me").json()["push_subscribed"] is True
    h.engine.push_health.taken()
    h.clock.t += timedelta(hours=7)
    assert client.get("/api/me").json()["push_reaching"] is False
    assert client.post("/api/push/ack", json={"stage": "received"}).status_code == 200
    me = client.get("/api/me").json()
    assert me["push_reaching"] is True and me["last_push_received"]


def test_the_platform_reads_whether_pushes_reach_a_phone_with_the_ingest_token(tmp_path):
    h = make_harness(at(2026, 11, 7, 10))
    keys = VapidKeys(tmp_path / "v.pem")
    keys.ensure()
    client = TestClient(create_app(h.engine, keys, "tok", DeviceRegistry(tmp_path / "d.db")))
    assert client.get("/api/push/health").status_code == 401
    h.store.add_subscription(SUB, at(2026, 11, 7), 3)
    h.engine.push_health.taken()
    h.clock.t += timedelta(hours=7)
    r = client.get("/api/push/health", headers={"authorization": "Bearer tok"}).json()
    assert (r["reaching"], r["subscriptions"]) == (False, 1)
