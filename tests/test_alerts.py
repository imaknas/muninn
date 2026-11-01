from __future__ import annotations

import json

from conftest import at, make_harness

from muninn.core.models import InboxItem


def alert(h, title, key, hours_ago=1):
    t = h.clock.now().replace(minute=0) if hours_ago == 0 else at(2026, 10, 22, 10 - hours_ago)
    return h.store.add_inbox(InboxItem(None, t, "health", "alert", title, "", {"actions": ["Wrap up now"]}, key))


def test_a_reminder_seen_by_saying_so_comes_back_on_undo():
    h = make_harness(at(2026, 10, 22, 10))
    a = alert(h, "23:30 之後還在工作", "anomaly:late-night")
    alert(h, "23:30 之後還在工作", "anomaly:late-night", hours_ago=2)  # the same topic again: one row, both ids
    late = lambda: [x for x in h.engine.views().alerts() if x["key"] == "anomaly:late-night"]  # noqa: E731
    assert late()[0]["times"] == 2
    assert h.engine.about("alert", str(a.id))["title"] == "23:30 之後還在工作"  # an alert can be what is discussed
    c = h.engine.conversation()
    h.cloud.reply = h.local.reply = json.dumps({"actions": [{"tool": "alert_seen", "args": {}}], "reply": ""})
    [outcome] = c.execute_turn(c.understand_turn(None, "知道了", {"kind": "alert", "id": str(a.id)})).outcomes
    assert outcome.text == "看過了:23:30 之後還在工作" and outcome.action_id
    assert late() == []  # both read
    assert h.engine.undo_action(outcome.action_id)
    assert late()[0]["times"] == 2


def test_all_reminders_at_once_through_the_same_action_as_the_button(web):
    h, client, devices = web
    client.post("/api/pair", json={"code": devices.new_pairing_code()})
    alert(h, "研究時數落後", "phd:goal:research-hours")
    b = alert(h, "23:30 之後還在工作", "anomaly:late-night", hours_ago=2)
    r = client.post("/api/inbox/read", json={"ids": [b.id], "title": "23:30 之後還在工作"}).json()
    assert r["ok"] and r["text"] == "看過了:23:30 之後還在工作" and r["action_id"]
    c = h.engine.conversation()
    h.cloud.reply = h.local.reply = json.dumps({"actions": [{"tool": "alert_seen", "args": {"all": True}}], "reply": ""})
    [outcome] = c.execute_turn(c.understand_turn(None, "提醒都看過了")).outcomes
    assert outcome.text.startswith("看過了:") and h.engine.views().alerts() == []
