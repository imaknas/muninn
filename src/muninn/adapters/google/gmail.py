"""Gmail: read new inbox mail, save reply drafts. Never sends."""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

import httpx

from muninn.adapters.postings import html_to_text
from muninn.core.models import MailMessage

from .oauth import GoogleAuthError, GoogleOAuth

log = logging.getLogger(__name__)
API = "https://gmail.googleapis.com/gmail/v1/users/me"
MAX_TEXT = 8000


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def body_text(payload: dict) -> str:
    """Prefer text/plain, fall back to text/html as text."""
    plain, html = [], []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and mime == "text/plain":
            plain.append(_b64(data).decode(errors="replace"))
        elif data and mime == "text/html":
            html.append(_b64(data).decode(errors="replace"))
        for p in part.get("parts") or ():
            walk(p)

    walk(payload)
    text = "\n".join(plain) if plain else html_to_text("\n".join(html))
    return text[:MAX_TEXT]


class GmailMailbox:
    def __init__(self, oauth: GoogleOAuth, name: str = "personal", kind: str = "personal",
                 query: str = "in:inbox -category:promotions -category:social", initial_lookback: timedelta = timedelta(days=3),
                 per_fetch: int = 25):
        self.name = name
        self.kind = kind
        self._oauth = oauth
        self._query = query
        self._lookback = initial_lookback
        self._per_fetch = per_fetch

    def available(self) -> bool:
        return self._oauth.authorized

    def fetch(self, cursor: Any, now: datetime) -> tuple[list[MailMessage], Any]:
        since = int(cursor) if cursor else int((now - self._lookback).timestamp())
        try:
            listing = self._oauth.get(f"{API}/messages", q=f"{self._query} after:{since}", maxResults=self._per_fetch)
        except (GoogleAuthError, httpx.HTTPError) as e:
            log.warning("gmail %s: %s", self.name, e)
            return [], cursor
        out, newest = [], since
        for ref in listing.get("messages", []):
            m = self._message(ref["id"])
            newest = max(newest, int(m.ts.timestamp()))
            out.append(m)
        return sorted(out, key=lambda x: x.ts), newest

    def cursor_of(self, m: MailMessage) -> Any:
        return int(m.ts.timestamp())

    def _message(self, message_id: str) -> MailMessage:
        m = self._oauth.get(f"{API}/messages/{message_id}", format="full")
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        return MailMessage(
            id=m["id"], thread_id=m["threadId"], account=self.name, account_kind=self.kind,
            sender=headers.get("from", ""), to=headers.get("to", ""), subject=headers.get("subject", ""),
            ts=datetime.fromtimestamp(int(m["internalDate"]) / 1000, timezone.utc),
            text=body_text(m.get("payload", {})), message_id=headers.get("message-id", ""),
            web_url=f"https://mail.google.com/mail/u/0/#all/{m['threadId']}",
        )

    def get(self, message_id: str) -> MailMessage | None:
        try:
            return self._message(message_id)
        except (GoogleAuthError, httpx.HTTPError) as e:
            log.warning("gmail %s: cannot read %s: %s", self.name, message_id, e)
            return None

    def create_draft(self, reply_to: MailMessage, body: str) -> str:
        msg = EmailMessage()
        msg["To"] = reply_to.sender
        subject = reply_to.subject
        msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        if reply_to.message_id:
            msg["In-Reply-To"] = reply_to.message_id
            msg["References"] = reply_to.message_id
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        res = self._oauth.send("POST", f"{API}/drafts", {"message": {"raw": raw, "threadId": reply_to.thread_id}})
        return res["id"]
