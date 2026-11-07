"""The calendar as a screen shows it: what was last read, at once, with the calendar asked again behind it when that is
over a minute old. Asking Google takes around a second, and no screen should wait for that, nor anyone behind it. What
is kept is in the store: it outlasts a restart, and writing it when it changed is how open screens come to be told
(the screens' fingerprint has it). What Muninn itself decides from the calendar reads the calendar, not this."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable

from muninn.core.models import CalendarEvent
from muninn.core.ports import StateStore

log = logging.getLogger(__name__)
EVENTS = "calendar:known"  # {read: its events as last read}
ASKED = "calendar:asked"  # {read: when it was last asked, "forgot": when Muninn last changed the calendar}
KEEP = timedelta(seconds=60)
LAST_FOR = timedelta(days=1)  # a read not asked for this long (yesterday's day) is dropped


def _in_background(work: Callable[[], None]) -> None:
    threading.Thread(target=work, name="calendar-read", daemon=True).start()


class KnownCalendar:
    def __init__(self, calendar: Any, store: StateStore, now: Callable[[], datetime], keep: timedelta = KEEP,
                 start: Callable[[Callable[[], None]], None] = _in_background):
        self._calendar, self._store, self._now, self._keep, self._start = calendar, store, now, keep, start
        self._guard = threading.Lock()
        self._going: set[str] = set()

    def available(self) -> bool:
        """Always: what is known can be shown, and a calendar that can't be read is found out behind it."""
        return True

    def own_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        return self._known("own_events", start, end)

    def events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        return self._known("events", start, end)

    def forget(self) -> None:
        """Muninn changed the calendar: what was read before now is asked again at the next look."""
        now = self._now().isoformat()
        self._store.update_state(ASKED, lambda asked: {**(asked or {}), "forgot": now})

    def _known(self, what: str, start: datetime, end: datetime) -> list[CalendarEvent]:
        read = f"{what} {start.isoformat()} {end.isoformat()}"
        if self._due(read):
            self._ask(read, what, start, end)
        return [CalendarEvent(e["id"], datetime.fromisoformat(e["start"]), datetime.fromisoformat(e["end"]), e["title"], e["calendar"])
                for e in (self._store.get_state(EVENTS) or {}).get(read, [])]

    def _due(self, read: str) -> bool:
        asked = self._store.get_state(ASKED) or {}
        at = asked.get(read)
        if at is None or (asked.get("forgot") and asked["forgot"] >= at):
            return True
        return self._now() - datetime.fromisoformat(at) >= self._keep

    def _ask(self, read: str, what: str, start: datetime, end: datetime) -> None:
        with self._guard:
            if read in self._going:
                return
            self._going.add(read)
        self._start(lambda: self._read(read, what, start, end))

    def _read(self, read: str, what: str, start: datetime, end: datetime) -> None:
        asked_at = self._now()  # before asking: a change made while it is on its way is not in the answer
        try:
            events = getattr(self._calendar, what)(start, end) if self._calendar.available() else []
            self._keep_events(read, [{"id": e.id, "start": e.start.isoformat(), "end": e.end.isoformat(), "title": e.title,
                                      "calendar": e.calendar} for e in events])
        except Exception as e:  # what is known stays shown; asked again in a minute, not on every read
            log.warning("calendar read failed: %s", e)
        finally:
            self._asked(read, asked_at)
            with self._guard:
                self._going.discard(read)

    def _keep_events(self, read: str, events: list[dict[str, Any]]) -> None:
        if ((self._store.get_state(EVENTS) or {}).get(read)) != events:  # unchanged: no write, so no screen is told
            self._store.update_state(EVENTS, lambda known: {**(known or {}), read: events})

    def _asked(self, read: str, at: datetime) -> None:
        def change(asked: Any) -> dict[str, Any]:
            kept = {k: t for k, t in (asked or {}).items() if k == "forgot" or at - datetime.fromisoformat(t) < LAST_FOR}
            return {**kept, read: at.isoformat()}

        self._store.update_state(ASKED, change)
        self._drop_events_no_longer_asked()

    def _drop_events_no_longer_asked(self) -> None:
        asked = self._store.get_state(ASKED) or {}
        known = self._store.get_state(EVENTS) or {}
        if any(k not in asked for k in known):
            self._store.update_state(EVENTS, lambda now: {k: v for k, v in (now or {}).items() if k in asked})
