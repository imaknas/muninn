"""Combine a writable calendar with read-only ones for busy time."""

from __future__ import annotations

from datetime import datetime

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
