"""A read of a screen stays quick: what it needs many times is worked out once while nothing changed, the calendar is
not asked on each read, and a request that kept someone waiting is said in the log."""

from __future__ import annotations

from datetime import timedelta

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from conftest import at, make_harness
from muninn.adapters import plans_yaml
from muninn.adapters.calendar_merge import RecentCalendar
from muninn.adapters.plans_yaml import YamlPlanRepo
from muninn.adapters.store_sqlite import SqliteStore
from muninn.adapters.web.slow_requests import SlowRequests
from muninn.core.models import Observation


def write_plan(tmp_path, title="Garden"):
    d = tmp_path / "garden"
    d.mkdir(exist_ok=True)
    (d / "plan.yaml").write_text(f"domain:\n  id: garden\n  title: {title}\n")
    return d


def test_a_plan_is_parsed_again_only_when_its_files_changed(tmp_path, monkeypatch):
    d = write_plan(tmp_path)
    parsed = []
    real = plans_yaml.parse_plan
    monkeypatch.setattr(plans_yaml, "parse_plan", lambda *a: parsed.append(a) or real(*a))
    repo = YamlPlanRepo(tmp_path, "UTC")
    assert [p.title for p in repo.load()] == ["Garden"]
    repo.load()
    assert len(parsed) == 1
    write_plan(tmp_path, "Roses")  # by any process
    assert [p.title for p in repo.load()] == ["Roses"]
    assert len(parsed) == 2
    (d / "applications.yaml").write_text("- {target: acme, status: todo}\n")  # the tracker, come into being since
    assert repo.load()[0].tracker
    assert len(parsed) == 3
    (d / "applications.yaml").unlink()
    assert not repo.load()[0].tracker
    (d / "plan.yaml").unlink()
    assert repo.load() == []


def obs(key, repo, domain=None):
    return Observation("git", at(2026, 11, 7), "commit", key, {"repo": repo} if repo else {}, domain)


def test_what_is_left_unplaced_is_counted_again_once_the_store_was_written_to(tmp_path):
    s = SqliteStore(tmp_path / "m.db")
    s.add_observations([obs("a", "rose-bot"), obs("b", "rose-bot"), obs("c", ""), obs("d", "seed-log", "garden")])
    assert s.unattributed_repos(100) == {"rose-bot": 2}
    other = SqliteStore(tmp_path / "m.db")  # another process
    other.add_observations([obs("e", "tulip")])
    assert s.unattributed_repos(100) == {"rose-bot": 2, "tulip": 1}
    s.add_observations([obs("f", "tulip")])  # by this process
    assert s.unattributed_repos(100) == {"rose-bot": 2, "tulip": 2}
    s.reattribute("f", "garden", None, 0)
    s.reattribute("a", "garden", None, 0)
    assert s.unattributed_repos(100) == {"rose-bot": 1, "tulip": 1}


class Calendar:
    def __init__(self):
        self.asked = []
        self.down = False

    def available(self):
        self.asked.append("available")
        return True

    def own_events(self, start, end):
        self.asked.append("own_events")
        if self.down:
            raise OSError("unreachable")
        return ["block"]

    def events(self, start, end):
        self.asked.append("events")
        return ["dentist"]

    def busy(self, start, end):
        self.asked.append("busy")
        return []

    def create(self, title, start, end, description=""):
        return "new"

    def delete(self, event_id):
        pass


def test_the_calendar_is_asked_once_a_minute_but_what_muninn_changes_shows_at_once():
    h = make_harness(at(2026, 11, 7, 10))
    inner = Calendar()
    cal = RecentCalendar(inner, h.clock.now)
    day = (at(2026, 11, 7), at(2026, 11, 8))
    assert cal.own_events(*day) == cal.own_events(*day) == ["block"]
    assert cal.available() and cal.available()
    assert inner.asked == ["own_events", "available"]
    cal.busy(*day), cal.busy(*day)  # where a block may go is never from memory
    assert inner.asked.count("busy") == 2
    h.clock.t += timedelta(seconds=61)
    cal.own_events(*day)
    assert inner.asked.count("own_events") == 2
    cal.create("Prune", *day)
    cal.own_events(*day)
    assert inner.asked.count("own_events") == 3
    cal.delete("x")
    cal.own_events(*day)
    assert inner.asked.count("own_events") == 4


def test_a_calendar_that_cannot_be_reached_fails_each_read_without_being_waited_for_each_time():
    h = make_harness(at(2026, 11, 7, 10))
    inner = Calendar()
    inner.down = True
    cal = RecentCalendar(inner, h.clock.now)
    day = (at(2026, 11, 7), at(2026, 11, 8))
    for _ in range(2):
        with pytest.raises(OSError):
            cal.own_events(*day)
    assert inner.asked == ["own_events"]
    inner.down = False
    h.clock.t += timedelta(seconds=61)
    assert cal.own_events(*day) == ["block"]


def test_a_request_that_kept_someone_waiting_is_said_in_the_log():
    said = []
    clock = iter([0.0, 2.5, 10.0, 10.1])
    app = SlowRequests(Starlette(routes=[Route("/api/home", lambda r: PlainTextResponse("ok"))]),
                       log=lambda *a: said.append(a[0] % a[1:]), timer=lambda: next(clock))
    client = TestClient(app)
    assert client.get("/api/home?x=secret").text == "ok"
    assert client.get("/api/home").text == "ok"
    assert said == ["slow request: GET /api/home took 2.5s"]
