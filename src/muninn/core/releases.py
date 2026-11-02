"""Telling the user a newer Muninn is out: once a day it asks which release is the latest, and each newer one is
said once, as what kind of update it is (the version says it: a new major version breaks something, a new minor one
adds features, a patch only fixes), what it brings, and how to update this installation. Features and breaking
changes come with a push; fixes wait quietly with the other reminders."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Protocol, Sequence

from packaging.version import InvalidVersion, Version

from .language import Language
from .models import InboxItem
from .ports import Clock, StateStore
from .timewin import local_date

log = logging.getLogger(__name__)

LISTED = 6  # changes of each kind named in the notice; the rest are counted


@dataclass(frozen=True)
class Notes:
    """What one release brings, from its notes."""

    version: str
    new: Sequence[str] = field(default_factory=tuple)
    fixed: Sequence[str] = field(default_factory=tuple)


class ReleaseFeed(Protocol):
    def latest(self) -> str | None:
        """The newest stable version published (None: none)."""
        ...

    def notes(self, after: str, upto: str) -> Sequence[Notes]:
        """The notes of each release newer than `after`, up to `upto`; empty when they cannot be read."""
        ...


def kind_of(current: str, latest: str) -> str | None:
    """"breaking", "features" or "fixes": what updating from `current` to `latest` is; None when `latest` is not
    newer, or either is not a version."""
    try:
        now, new = Version(current), Version(latest)
    except InvalidVersion:
        return None
    if new <= now:
        return None
    if new.major != now.major:
        return "breaking"
    return "features" if new.minor != now.minor else "fixes"


class ReleaseNews:
    def __init__(self, store: StateStore, clock: Clock, tz: str, feed: ReleaseFeed | None, current: str, install: str,
                 report: Callable[[InboxItem, datetime], Any], keep: Callable[[InboxItem], Any], *, words: Language):
        """`feed`: where releases are read (None: not checked); `current`: the version running; `install`: how this
        Muninn was installed ("uv tool", "container" or "other"), which says how to update it; `report`: says an
        item with a push, `keep`: keeps it with the reminders without one."""
        self._store, self._clock, self._tz, self._feed = store, clock, tz, feed
        self._current, self._install, self._report, self._keep = current, install, report, keep
        self.words = words

    def jobs(self) -> list[Any]:
        from .actors import Job

        return [Job("checking for a newer Muninn", self.check)] if self._feed is not None else []

    def check(self) -> InboxItem | None:
        """At most once a day: the newest release, said once when it is newer than what runs."""
        assert self._feed is not None
        now = self._clock.now()
        today = f"release-check:{local_date(now, self._tz).isoformat()}"
        if not self._store.claim(today, now):
            return None
        try:
            latest = self._feed.latest()
        except Exception as e:  # offline, or the index is down: tried again on the next tick
            self._store.release(today)
            log.info("could not tell the latest release: %s", e)
            return None
        kind = kind_of(self._current, latest) if latest else None
        if kind is None or not self._store.claim(f"release:{latest}", now):
            return None
        item = self.notice(latest, kind, self._feed.notes(self._current, latest), now)
        if kind == "fixes":
            self._keep(item)
        else:
            self._report(item, now)
        return item

    def notice(self, latest: str, kind: str, notes: Sequence[Notes], now: datetime) -> InboxItem:
        w = self.words
        parts = []
        for label, changes in (("releases.new", [c for n in notes for c in n.new]),
                               ("releases.fixed", [c for n in notes for c in n.fixed])):
            if changes:
                lines = [f"- {c}" for c in changes[:LISTED]]
                if len(changes) > LISTED:
                    lines.append(w("releases.more", n=len(changes) - LISTED))
                parts.append(w(label) + "\n" + "\n".join(lines))
        parts.append(w(f"releases.how_{self._install.replace(' ', '_')}"))
        return InboxItem(None, now, "muninn", "info", w(f"releases.title_{kind}", version=latest, current=self._current),
                         "\n\n".join(parts), {"release": latest, "update": kind})
