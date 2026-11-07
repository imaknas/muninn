"""A read of a screen stays quick: what it needs many times is worked out once while nothing changed, it shows the
calendar as known with the calendar asked behind it, and a request that kept someone waiting is said in the log."""

from __future__ import annotations

from datetime import timedelta

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from conftest import at, make_harness
from muninn.adapters import plans_yaml
from muninn.adapters.calendar_merge import CalendarWithExtraBusy
from muninn.adapters.channels.webpush import VapidKeys
from muninn.adapters.known_calendar import ASKED, EVENTS, KnownCalendar
from muninn.adapters.plans_yaml import YamlPlanRepo
from muninn.adapters.store_sqlite import SqliteStore
from muninn.adapters.web.app import create_app
from muninn.adapters.web.devices import DeviceRegistry
from muninn.adapters.web.slow_requests import SlowRequests
from muninn.core.models import CalendarEvent, Observation


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
    def __init__(self, title="Prune"):
        self.asked = []
        self.title = title
        self.down = False

    def available(self):
        return True

    def own_events(self, start, end):
        self.asked.append("own_events")
        if self.down:
            raise OSError("unreachable")
        return [CalendarEvent("e1", start, end, self.title)]

    def create(self, title, start, end, description=""):
        return "new"


DAY = (at(2026, 11, 7), at(2026, 11, 8))


def known(h, inner, **more):
    waiting = []
    return KnownCalendar(inner, h.store, h.clock.now, start=waiting.append, **more), waiting


def run(waiting):
    while waiting:
        waiting.pop()()


def test_a_screen_gets_what_is_known_at_once_and_the_calendar_is_asked_behind_it():
    h = make_harness(at(2026, 11, 7, 10))
    inner = Calendar()
    cal, waiting = known(h, inner)
    assert cal.own_events(*DAY) == [] and inner.asked == []  # nothing known yet: nobody waits for it
    assert len(waiting) == 1
    cal.own_events(*DAY)
    assert len(waiting) == 1  # one asking at a time
    run(waiting)
    assert [e.title for e in cal.own_events(*DAY)] == ["Prune"] and not waiting
    assert cal.own_events(*DAY)[0].start == DAY[0]
    h.clock.t += timedelta(seconds=61)
    inner.title = "Mulch"
    assert [e.title for e in cal.own_events(*DAY)] == ["Prune"]  # still at once, and asked again behind it
    run(waiting)
    assert [e.title for e in cal.own_events(*DAY)] == ["Mulch"]
    assert KnownCalendar(inner, h.store, h.clock.now, start=waiting.append).own_events(*DAY)[0].title == "Mulch"  # after a restart


def test_open_screens_are_told_only_when_what_they_show_changed():
    h = make_harness(at(2026, 11, 7, 10))
    inner = Calendar()
    cal, waiting = known(h, inner)
    cal.own_events(*DAY)
    run(waiting)
    shown = h.store.get_state(EVENTS)
    h.clock.t += timedelta(seconds=61)
    cal.own_events(*DAY)
    run(waiting)
    assert h.store.get_state(EVENTS) == shown  # asked, nothing new
    h.clock.t += timedelta(seconds=61)
    inner.title = "Mulch"
    cal.own_events(*DAY)
    run(waiting)
    assert h.store.get_state(EVENTS) != shown


def test_what_muninn_changes_is_asked_again_at_the_next_look_even_past_a_read_on_its_way():
    h = make_harness(at(2026, 11, 7, 10))
    inner = Calendar()
    cal, waiting = known(h, inner)
    combined = CalendarWithExtraBusy(inner, [], changed=cal.forget)
    cal.own_events(*DAY)
    run(waiting)
    assert cal.own_events(*DAY) and not waiting
    h.clock.t += timedelta(seconds=1)
    combined.create("Weed", *DAY)
    cal.own_events(*DAY)
    assert len(waiting) == 1
    run(waiting)
    # a read that started before the change is not taken for one after it
    h.clock.t += timedelta(seconds=1)
    combined.create("Water", *DAY)
    h.store.update_state(ASKED, lambda asked: {**asked, next(k for k in asked if k != "forgot"): at(2026, 11, 7, 9).isoformat()})
    cal.own_events(*DAY)
    assert len(waiting) == 1


def test_a_calendar_that_cannot_be_reached_leaves_what_is_known_and_is_not_asked_on_every_read():
    h = make_harness(at(2026, 11, 7, 10))
    inner = Calendar()
    cal, waiting = known(h, inner)
    cal.own_events(*DAY)
    run(waiting)
    inner.down = True
    h.clock.t += timedelta(seconds=61)
    cal.own_events(*DAY)
    run(waiting)
    assert [e.title for e in cal.own_events(*DAY)] == ["Prune"] and not waiting
    assert inner.asked == ["own_events", "own_events"]


def test_yesterdays_day_is_not_kept():
    h = make_harness(at(2026, 11, 7, 10))
    cal, waiting = known(h, Calendar())
    cal.own_events(*DAY)
    run(waiting)
    h.clock.t += timedelta(days=2)
    cal.own_events(at(2026, 11, 9), at(2026, 11, 10))
    run(waiting)
    assert len(h.store.get_state(EVENTS)) == 1


def test_the_home_screen_does_not_ask_the_calendar_itself_but_what_is_shown_comes_from_it():
    h = make_harness(at(2026, 11, 7, 10))
    inner = Calendar()
    cal, waiting = known(h, inner)
    h.engine.shown_calendar = cal
    assert h.engine.home_view()["schedule"] == [] and inner.asked == []
    run(waiting)
    assert [e["title"] for e in h.engine.home_view()["schedule"]] == ["Prune"]


def test_a_change_in_the_calendar_tells_the_open_screens(tmp_path):
    h = make_harness(at(2026, 11, 7, 10))
    keys = VapidKeys(tmp_path / "v.pem")
    keys.ensure()
    screens = create_app(h.engine, keys, "tok", DeviceRegistry(tmp_path / "d.db")).state.screens
    cal, waiting = known(h, Calendar())
    screens.check()
    cal.own_events(*DAY)
    assert not screens.check()  # only asked
    run(waiting)
    assert screens.check()


def test_a_request_that_kept_someone_waiting_is_said_in_the_log():
    said = []
    clock = iter([0.0, 2.5, 10.0, 10.1])
    app = SlowRequests(Starlette(routes=[Route("/api/home", lambda r: PlainTextResponse("ok"))]),
                       log=lambda *a: said.append(a[0] % a[1:]), timer=lambda: next(clock))
    client = TestClient(app)
    assert client.get("/api/home?x=secret").text == "ok"
    assert client.get("/api/home").text == "ok"
    assert said == ["slow request: GET /api/home took 2.5s"]
