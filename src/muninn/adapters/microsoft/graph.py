"""Microsoft 365 through Microsoft Graph: work mail (read only) and the work
calendar's busy time. Read-only by design: Muninn writes nothing to the
work account."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from muninn.adapters.oauth import OAuth2Client, OAuthError, Provider
from muninn.adapters.postings import html_to_text
from muninn.core.models import MailMessage

log = logging.getLogger(__name__)
API = "https://graph.microsoft.com/v1.0/me"
MAX_TEXT = 8000

MICROSOFT = Provider(
    name="microsoft",
    auth_url="https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize",
    token_url="https://login.microsoftonline.com/organizations/oauth2/v2.0/token",
    scopes=("offline_access", "User.Read", "Mail.Read", "Calendars.Read"),
    auth_params={"response_mode": "query", "prompt": "select_account"},
    scope_in_token_requests=True,
)


def MicrosoftOAuth(client_file: Path, token_file: Path, state_file: Path, redirect_uri: str | None,
                   http: httpx.Client | None = None) -> OAuth2Client:
    return OAuth2Client(MICROSOFT, client_file, token_file, state_file, redirect_uri, http)


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _graph_time(value: dict) -> datetime:
    """Graph dateTime without offset, in the given timeZone (we ask for UTC)."""
    dt = datetime.fromisoformat(value["dateTime"][:26])
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


class GraphMailbox:
    """Inbox mail of the work account. Kind 'work': sensitive (local model unless routing is cloud)."""

    def __init__(self, oauth: OAuth2Client, name: str = "work", kind: str = "work",
                 initial_lookback: timedelta = timedelta(days=3), per_fetch: int = 25):
        self.name = name
        self.kind = kind
        self._oauth = oauth
        self._lookback = initial_lookback
        self._per_fetch = per_fetch

    def available(self) -> bool:
        return self._oauth.authorized

    @staticmethod
    def _addr(x: dict | None) -> str:
        e = (x or {}).get("emailAddress") or {}
        return f"{e.get('name', '')} <{e.get('address', '')}>".strip()

    def _message(self, m: dict) -> MailMessage:
        body = m.get("body") or {}
        text = body.get("content", "")
        if body.get("contentType") == "html":
            text = html_to_text(text)
        return MailMessage(
            id=m["id"], thread_id=m.get("conversationId", m["id"]), account=self.name, account_kind=self.kind,
            sender=self._addr(m.get("from")), to=", ".join(self._addr(t) for t in m.get("toRecipients") or []),
            subject=m.get("subject") or "", ts=_ts(m["receivedDateTime"]), text=text[:MAX_TEXT],
            message_id=m.get("internetMessageId", ""), web_url=m.get("webLink", ""),
        )

    def fetch(self, cursor: Any, now: datetime) -> tuple[list[MailMessage], Any]:
        since = cursor or (now - self._lookback).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            res = self._oauth.get(
                f"{API}/mailFolders/inbox/messages",
                headers={"Prefer": 'outlook.body-content-type="text"'},
                **{"$filter": f"receivedDateTime gt {since}", "$orderby": "receivedDateTime asc", "$top": self._per_fetch,
                   "$select": "id,conversationId,from,toRecipients,subject,receivedDateTime,body,internetMessageId,webLink"},
            )
        except (OAuthError, httpx.HTTPError) as e:
            log.warning("graph mail %s: %s", self.name, e)
            return [], cursor
        out = [self._message(m) for m in res.get("value", [])]
        return out, (self.cursor_of(max(out, key=lambda m: m.ts)) if out else since)

    def cursor_of(self, m: MailMessage) -> Any:
        return m.ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def get(self, message_id: str) -> MailMessage | None:
        try:
            m = self._oauth.get(f"{API}/messages/{message_id}", headers={"Prefer": 'outlook.body-content-type="text"'})
            return self._message(m)
        except (OAuthError, httpx.HTTPError) as e:
            log.warning("graph mail %s: cannot read message: %s", self.name, e)
            return None

    def create_draft(self, reply_to: MailMessage, body: str) -> str:
        raise PermissionError("the work account is read-only for Muninn")


class GraphBusy:
    """Busy time from the work calendar (no titles kept)."""

    def __init__(self, oauth: OAuth2Client):
        self._oauth = oauth

    def available(self) -> bool:
        return self._oauth.authorized

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
        try:
            res = self._oauth.get(
                f"{API}/calendarView", headers={"Prefer": 'outlook.timezone="UTC"'},
                startDateTime=start.astimezone(timezone.utc).isoformat(), endDateTime=end.astimezone(timezone.utc).isoformat(),
                **{"$select": "start,end,showAs,isCancelled", "$top": 100},
            )
        except (OAuthError, httpx.HTTPError) as e:
            log.warning("work calendar unavailable: %s", e)
            return []
        return sorted(
            (_graph_time(e["start"]), _graph_time(e["end"]))
            for e in res.get("value", [])
            if not e.get("isCancelled") and e.get("showAs") not in ("free", "workingElsewhere")
        )

