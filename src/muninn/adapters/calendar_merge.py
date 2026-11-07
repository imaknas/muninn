"""Combine a writable calendar with read-only ones for busy time."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable

from muninn.core.models import CalendarEvent


class CalendarWithExtraBusy:
    """A writable calendar whose busy time also includes other, read-only
    calendars (e.g. the work calendar), so blocks avoid both."""

    def __init__(self, primary, extra):
        self._primary = primary
        self._extra = list(extra)

    def available(self) -> bool:
        return self._primary.available()

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        out = list(self._primary.busy(start, end))
        for src in self._extra:
            if src.available():
                out += src.busy(start, end)
        return sorted(out)

    def own_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        return self._primary.own_events(start, end)

    def events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        return self._primary.events(start, end)

    def create(self, title: str, start: datetime, end: datetime, description: str = "") -> str:
        return self._primary.create(title, start, end, description)

    def delete(self, event_id: str) -> None:
        self._primary.delete(event_id)


class RecentCalendar:
    """A calendar whose reads are remembered for a moment: each read of a screen shows the day's events, and asking
    Google takes around a second, with every other request waiting behind it. A calendar that can't be reached is
    remembered too, or every read would wait out the timeout. What Muninn changes itself is seen at once. What the free
    time is worked out from is always asked anew."""

    def __init__(self, calendar: Any, now: Callable[[], datetime], keep: timedelta = timedelta(seconds=60)):
        self._calendar = calendar
        self._now = now
        self._keep = keep
        self._read: dict[tuple, tuple[datetime, Any, Exception | None]] = {}

    def _remembered(self, *what: Any) -> Any:
        now = self._now()
        kept = self._read.get(what)
        if kept is None or now - kept[0] >= self._keep:
            value, failed = None, None
            try:
                value = getattr(self._calendar, what[0])(*what[1:])
            except Exception as e:
                failed = e
            self._read = {k: v for k, v in self._read.items() if now - v[0] < self._keep}
            kept = self._read[what] = (now, value, failed)
        if kept[2] is not None:
            raise kept[2]
        return kept[1]

    def available(self) -> bool:
        return self._remembered("available")

    def own_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        return list(self._remembered("own_events", start, end))

    def events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        return list(self._remembered("events", start, end))

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        return self._calendar.busy(start, end)

    def create(self, title: str, start: datetime, end: datetime, description: str = "") -> str:
        self._read = {}
        return self._calendar.create(title, start, end, description)

    def delete(self, event_id: str) -> None:
        self._read = {}
        self._calendar.delete(event_id)
