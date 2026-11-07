"""Combine a writable calendar with read-only ones for busy time."""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from muninn.core.models import CalendarEvent


class CalendarWithExtraBusy:
    """A writable calendar whose busy time also includes other, read-only
    calendars (e.g. the work calendar), so blocks avoid both."""

    def __init__(self, primary, extra, changed: Callable[[], None] = lambda: None):
        """`changed`: told each time Muninn has changed the calendar."""
        self._primary = primary
        self._extra = list(extra)
        self._changed = changed

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
        made = self._primary.create(title, start, end, description)
        self._changed()
        return made

    def delete(self, event_id: str) -> None:
        self._primary.delete(event_id)
        self._changed()
