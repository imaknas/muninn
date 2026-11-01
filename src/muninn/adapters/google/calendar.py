"""Google Calendar: busy time and events from every calendar the user shows,
writes only to the calendar named for Muninn (created by the user)."""

from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote

import httpx

from muninn.core.models import CalendarEvent

from .oauth import GoogleAuthError, GoogleOAuth

log = logging.getLogger(__name__)
API = "https://www.googleapis.com/calendar/v3"


def _parse(value: dict) -> datetime:
    if "dateTime" in value:
        return datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
    return datetime.fromisoformat(value["date"] + "T00:00:00+00:00")


class GoogleCalendar:
    def __init__(self, oauth: GoogleOAuth, own_calendar_name: str = "Muninn"):
        self._oauth = oauth
        self._name = own_calendar_name
        self._own_id: str | None = None

    def available(self) -> bool:
        if not self._oauth.authorized:
            return False
        try:
            return self._own() is not None
        except (GoogleAuthError, httpx.HTTPError) as e:
            log.warning("calendar unavailable: %s", e)
            return False

    def _calendars(self) -> list[dict]:
        return self._oauth.get(f"{API}/users/me/calendarList").get("items", [])

    def _own(self) -> str | None:
        if self._own_id is None:
            self._own_id = next((c["id"] for c in self._calendars() if c.get("summary") == self._name), None)
            if self._own_id is None:
                log.warning("no calendar named %r; create it in Google Calendar", self._name)
        return self._own_id

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        ids = [c["id"] for c in self._calendars() if c.get("selected", True) and c.get("accessRole") in ("owner", "writer", "reader")]
        body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": i} for i in ids]}
        res = self._oauth.send("POST", f"{API}/freeBusy", body)
        out = []
        for cal in res.get("calendars", {}).values():
            for b in cal.get("busy", []):
                out.append((datetime.fromisoformat(b["start"].replace("Z", "+00:00")), datetime.fromisoformat(b["end"].replace("Z", "+00:00"))))
        return sorted(out)

    def events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        own = self._own()
        out = []
        for c in self._calendars():
            if c["id"] == own or not c.get("selected", True):
                continue
            items = self._oauth.get(f"{API}/calendars/{quote(c['id'], safe='')}/events", timeMin=start.isoformat(), timeMax=end.isoformat(),
                                    singleEvents="true", orderBy="startTime", maxResults="100").get("items", [])
            for e in items:
                me = next((a for a in e.get("attendees") or [] if a.get("self")), None)
                if e.get("status") == "cancelled" or (me and me.get("responseStatus") == "declined"):
                    continue
                if "start" in e and "end" in e:
                    out.append(CalendarEvent(e["id"], _parse(e["start"]), _parse(e["end"]), e.get("summary", ""),
                                             c.get("summaryOverride") or c.get("summary", "")))
        return sorted(out, key=lambda ev: ev.start)

    def own_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        own = self._own()
        if own is None:
            return []
        items = self._oauth.get(f"{API}/calendars/{own}/events", timeMin=start.isoformat(), timeMax=end.isoformat(),
                                singleEvents="true", orderBy="startTime").get("items", [])
        return [CalendarEvent(e["id"], _parse(e["start"]), _parse(e["end"]), e.get("summary", "")) for e in items if e.get("status") != "cancelled"]

    def create(self, title: str, start: datetime, end: datetime, description: str = "") -> str:
        own = self._own()
        if own is None:
            raise GoogleAuthError(f"no calendar named {self._name!r}")
        body = {"summary": title, "description": description,
                "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
                "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 5}]}}
        return self._oauth.send("POST", f"{API}/calendars/{own}/events", body)["id"]

    def delete(self, event_id: str) -> None:
        own = self._own()
        if own is None:
            return
        try:
            self._oauth.send("DELETE", f"{API}/calendars/{own}/events/{event_id}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in (404, 410):
                raise
