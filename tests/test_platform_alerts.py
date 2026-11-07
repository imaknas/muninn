"""Alerts from the platform (contracts/platform-alerts.v1.md): one reminder per problem, said again each time the
platform says it again, pushed outside the daily limit but held through quiet hours, taken away when resolved."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from conftest import at, make_harness
from muninn.adapters.channels.webpush import VapidKeys
from muninn.adapters.web.app import create_app
from muninn.adapters.web.devices import DeviceRegistry
from muninn.core.platform_alerts import Refused, notice


def note(state="open", at_="2026-11-07T09:00:00Z", text="2 個排程工作失敗:nightly-backup 等", key="scheduler-jobs", **more):
    return notice({"v": 1, "source": "brunna", "key": key, "state": state, "at": at_, "text": text, **more})


def pushed(h) -> list[str]:
    return [m.title for batch in h.channel.sent for m in batch]


def shown(h) -> list[dict]:
    return [a for a in h.engine.views().alerts() if a["domain"] == "platform"]


def test_an_open_problem_is_one_reminder_said_again_each_time_the_platform_says_it_again():
    h = make_harness(at(2026, 11, 7, 17))
    pa = h.engine.platform_alerts
    assert pa.receive(note(detail="nightly-backup\nmail-sync")) == "new"
    assert pa.receive(note()) == "repeat"  # a retry of the same notice
    [a] = shown(h)
    assert (a["title"], a["times"]) == ("2 個排程工作失敗:nightly-backup 等", 1)
    h.engine.alerts.mark_read(a["ids"])  # seen is not resolved
    assert pa.receive(note(at_="2026-11-08T09:00:00Z", text="3 個排程工作失敗:nightly-backup 等")) == "reminded"
    [a] = shown(h)
    assert a["title"] == "3 個排程工作失敗:nightly-backup 等"
    assert pushed(h) == ["2 個排程工作失敗:nightly-backup 等", "3 個排程工作失敗:nightly-backup 等"]


def test_its_pushes_do_not_count_against_the_daily_limit():
    h = make_harness(at(2026, 11, 7, 17))
    for i in range(12):
        h.engine.platform_alerts.receive(note(key=f"k{i}", text=f"problem {i}"))
    assert len(pushed(h)) == 12


def test_quiet_hours_hold_the_push_until_they_end_if_the_problem_is_still_open():
    h = make_harness(at(2026, 11, 7, 23, 30))
    pa = h.engine.platform_alerts
    pa.receive(note(text="LINE 收不到訊息"))
    pa.receive(note(key="status-page", text="狀態頁打不開"))
    assert pushed(h) == [] and len(shown(h)) == 2
    assert pa.release_held() == []  # still quiet
    pa.receive(note("resolved", "2026-11-07T16:00:00Z", "狀態頁恢復了", key="status-page"))
    h.clock.t = at(2026, 11, 8, 8, 5)
    assert pa.release_held() == ["LINE 收不到訊息"]
    assert pa.release_held() == []
    assert pushed(h) == ["LINE 收不到訊息"]


def test_resolved_takes_the_reminder_away_and_says_once_without_a_push_what_works_again():
    h = make_harness(at(2026, 11, 7, 17))
    pa = h.engine.platform_alerts
    assert pa.receive(note("resolved", text="LINE 恢復了", key="line")) == "not_open"
    assert pa.receive(note(at_="2026-11-07T09:30:00Z", text="LINE 收不到訊息", key="line")) == "new"
    assert pa.receive(note("resolved", "2026-11-07T10:00:00Z", "LINE 恢復了", key="line")) == "resolved"
    assert pa.receive(note("resolved", "2026-11-07T10:00:00Z", "LINE 恢復了", key="line")) == "repeat"
    assert [a["title"] for a in shown(h)] == ["LINE 恢復了"]
    assert pushed(h) == ["LINE 收不到訊息"]
    # an older notice overtaken on the way changes nothing
    assert pa.receive(note(text="LINE 收不到訊息", key="line")) == "repeat"
    assert pa.receive(note(at_="2026-11-08T09:00:00Z", text="LINE 又收不到訊息", key="line")) == "new"


def test_what_was_kept_is_put_back_when_saying_it_fails_so_the_retry_is_not_taken_as_had():
    h = make_harness(at(2026, 11, 7, 17))
    pa = h.engine.platform_alerts
    real = h.channel.send
    h.channel.send = lambda messages: (_ for _ in ()).throw(OSError("push service down"))
    with pytest.raises(OSError):
        pa.receive(note())
    h.channel.send = real
    assert pa.receive(note()) == "new"


@pytest.mark.parametrize("bad,why", [
    ({"v": 2}, "v"),
    ({"source": "Brunna"}, "source"),
    ({"key": ""}, "key"),
    ({"state": "closed"}, "state"),
    ({"text": "two\nlines"}, "text"),
    ({"at": "2026-11-07 09:00"}, "at"),
    ({"at": "yesterday"}, "at"),
])
def test_a_notice_that_does_not_keep_to_the_contract_is_refused_with_why(bad, why):
    raw = {"v": 1, "source": "brunna", "key": "k", "state": "open", "at": "2026-11-07T09:00:00Z", "text": "t", **bad}
    with pytest.raises(Refused, match=why):
        notice(raw)


def test_the_platform_posts_with_the_ingest_token_and_only_a_200_says_it_was_had(tmp_path):
    h = make_harness(at(2026, 11, 7, 17))
    keys = VapidKeys(tmp_path / "v.pem")
    keys.ensure()
    client = TestClient(create_app(h.engine, keys, "tok", DeviceRegistry(tmp_path / "d.db")))
    body = {"v": 1, "source": "brunna", "key": "line", "state": "open", "at": "2026-11-07T09:00:00Z", "text": "LINE 收不到訊息",
            "extra": "ignored"}
    assert client.post("/api/alerts", json=body).status_code == 401
    r = client.post("/api/alerts", json=body, headers={"authorization": "Bearer tok"})
    assert (r.status_code, r.json()) == (200, {"ok": True, "outcome": "new"})
    r = client.post("/api/alerts", json={**body, "state": "gone"}, headers={"authorization": "Bearer tok"})
    assert r.status_code == 400 and "state" in r.json()["error"]
