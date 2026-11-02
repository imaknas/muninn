"""A newer Muninn is said once, as features, fixes or a breaking change, with what it brings and how to update."""

from __future__ import annotations

from datetime import timedelta

import httpx

from conftest import FakeClock, at, make_harness
from muninn.adapters.releases import PublishedReleases, notes_of
from muninn.adapters.store_sqlite import SqliteStore
from muninn.core.releases import Notes, ReleaseNews, kind_of
from muninn.languages import language
from muninn.upgrade import Release

EN = language("en")


class Feed:
    def __init__(self, latest="0.9.0", notes=(), fails=False):
        self.latest_version, self.listed, self.fails, self.asked = latest, list(notes), fails, 0

    def latest(self):
        self.asked += 1
        if self.fails:
            raise httpx.ConnectError("offline")
        return self.latest_version

    def notes(self, after, upto):
        return self.listed


def news(feed, current="0.8.1", install="uv tool", now=None):
    store, clock = SqliteStore(":memory:"), FakeClock(now or at(2026, 11, 3, 9))
    pushed, kept = [], []
    actor = ReleaseNews(store, clock, "Asia/Taipei", feed, current, install, lambda item, now: pushed.append(item),
                        kept.append, words=EN)
    return actor, clock, pushed, kept


def test_the_version_says_what_kind_of_update_it_is():
    assert kind_of("0.8.1", "0.9.0") == "features"
    assert kind_of("0.8.1", "0.8.2") == "fixes"
    assert kind_of("0.8.1", "1.0.0") == "breaking"
    assert kind_of("0.8.1", "0.8.1") is None
    assert kind_of("0.9.0", "0.8.1") is None
    assert kind_of("0.8.1+local", "0.9.0") == "features"
    assert kind_of("not-a-version", "0.9.0") is None


def test_new_features_are_said_with_a_push_listing_the_new_and_the_fixed_apart():
    feed = Feed("0.9.0", [Notes("0.9.0", ("Muninn reminds you of new releases",), ("A typo in the setup page",)),
                          Notes("0.8.2", (), ("The log no longer grows without end",))])
    actor, _, pushed, kept = news(feed)

    item = actor.check()

    assert pushed == [item] and kept == []
    assert "0.9.0" in item.title and "new features" in item.title and "0.8.1" in item.title
    new, fixed, how = item.body.split("\n\n")
    assert new == "New:\n- Muninn reminds you of new releases"
    assert fixed == "Fixed:\n- A typo in the setup page\n- The log no longer grows without end"
    assert "muninn upgrade" in how
    assert item.domain == "muninn" and item.kind == "info" and item.data["update"] == "features"


def test_fixes_wait_with_the_reminders_without_a_push():
    actor, _, pushed, kept = news(Feed("0.8.2", [Notes("0.8.2", (), ("A crash on start",))]))

    item = actor.check()

    assert pushed == [] and kept == [item]
    assert "with fixes" in item.title and "New:" not in item.body


def test_a_breaking_release_says_so():
    actor, _, pushed, _ = news(Feed("1.0.0"))

    item = actor.check()

    assert pushed == [item] and "major update" in item.title


def test_each_release_is_said_once_and_the_index_asked_once_a_day():
    feed = Feed("0.9.0")
    actor, clock, pushed, _ = news(feed)

    assert actor.check() is not None
    assert actor.check() is None and feed.asked == 1  # the same day: not asked again
    clock.t += timedelta(days=1)
    assert actor.check() is None and feed.asked == 2  # asked, but 0.9.0 was already said
    feed.latest_version = "0.10.0"
    clock.t += timedelta(days=1)
    assert actor.check() is not None and len(pushed) == 2


def test_an_index_that_cannot_be_reached_is_asked_again_on_the_next_tick():
    feed = Feed(fails=True)
    actor, _, pushed, kept = news(feed)

    assert actor.check() is None
    feed.fails = False
    assert actor.check() is not None and feed.asked == 2


def test_without_notes_the_release_is_still_said():
    actor, _, pushed, _ = news(Feed("0.9.0", []))

    item = actor.check()

    assert "new features" in item.title and item.body.startswith("To update")


def test_how_to_update_follows_the_installation_and_long_lists_are_counted():
    many = Notes("0.9.0", tuple(f"Feature {i}" for i in range(9)))
    actor, _, pushed, _ = news(Feed("0.9.0", [many]), install="container")

    item = actor.check()

    assert "- and 3 more" in item.body and "docker compose up -d --build" in item.body


def test_without_a_feed_there_is_no_job():
    actor, *_ = news(None)
    assert actor.jobs() == []


def test_a_tick_says_a_newer_release_once():
    h = make_harness(at(2026, 11, 3, 9))
    h.engine.release_news = ReleaseNews(h.store, h.clock, "Asia/Taipei", Feed("0.9.0"), "0.8.1", "uv tool",
                                        h.engine._report, h.store.add_inbox, words=h.engine.words)

    h.engine.tick()
    h.engine.tick()

    said = [i for i in h.store.inbox() if i.data.get("release")]
    assert [i.data["release"] for i in said] == ["0.9.0"]


BODY = """## v0.9.0 (2026-11-03)

### New

- **releases:** Muninn says when a newer release is out
- A plain one without a scope

### Fixed

- **setup:** The log is kept small

### Faster

- Startup takes a second less
"""


def test_release_notes_are_read_by_heading_without_their_scopes():
    notes = notes_of("0.9.0", BODY)

    assert notes.new == ("Muninn says when a newer release is out", "A plain one without a scope")
    assert notes.fixed == ("The log is kept small", "Startup takes a second less")


class Response:
    def __init__(self, status_code, data):
        self.status_code, self._data = status_code, data

    def json(self):
        return self._data


class Index:
    def latest(self):
        return Release("0.9.0", "muninn-ai==0.9.0")


def test_published_releases_give_the_notes_between_two_versions_newest_first():
    listed = [{"tag_name": v, "body": f"### Fixed\n\n- Fix in {v}\n", "draft": False, "prerelease": False}
              for v in ("v0.9.0", "v0.8.2", "v0.8.1", "v0.8.0")]
    listed.append({"tag_name": "v0.9.1", "body": "### Fixed\n\n- not out yet\n", "draft": True, "prerelease": False})
    feed = PublishedReleases(Index(), get=lambda url: Response(200, listed))

    assert feed.latest() == "0.9.0"
    assert [n.version for n in feed.notes("0.8.1", "0.9.0")] == ["0.9.0", "0.8.2"]


def test_unreadable_notes_are_none_rather_than_an_error():
    def offline(url):
        raise httpx.ConnectError("offline")

    assert PublishedReleases(Index(), get=offline).notes("0.8.1", "0.9.0") == []
    assert PublishedReleases(Index(), get=lambda url: Response(403, {"message": "rate limited"})).notes("0.8.1", "0.9.0") == []
