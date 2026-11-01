from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from conftest import at
from muninn.adapters.calendar_merge import CalendarWithExtraBusy
from muninn.adapters.microsoft.graph import MICROSOFT, GraphBusy, GraphMailbox, MicrosoftOAuth
from muninn.adapters.oauth import OAuth2Client


class FakeOAuth:
    authorized = True

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, url, headers=None, **params):
        self.calls.append((url, params))
        for key, value in self.responses.items():
            if url.endswith(key):
                return value
        raise AssertionError(url)


MSG = {"id": "AAMk1", "conversationId": "c1", "from": {"emailAddress": {"name": "Boss", "address": "boss@corp.example"}},
       "toRecipients": [{"emailAddress": {"name": "Me", "address": "me@corp.example"}}], "subject": "Sprint review",
       "receivedDateTime": "2026-10-21T02:00:00Z", "body": {"contentType": "text", "content": "Please prepare slides."},
       "internetMessageId": "<x@corp>", "webLink": "https://outlook.office365.com/owa/?ItemID=AAMk1"}


def test_graph_mailbox_reads_work_mail_as_work():
    box = GraphMailbox(FakeOAuth({"/mailFolders/inbox/messages": {"value": [MSG]}}))
    [m], cursor = box.fetch(None, at(2026, 10, 21, 12))
    assert m.account_kind == "work" and m.sender == "Boss <boss@corp.example>" and m.text == "Please prepare slides."
    assert m.web_url.startswith("https://outlook") and cursor == "2026-10-21T02:00:00Z"
    with pytest.raises(PermissionError):
        box.create_draft(m, "no")  # read-only


def test_graph_busy_skips_free_and_cancelled():
    oauth = FakeOAuth({"/calendarView": {"value": [
        {"start": {"dateTime": "2026-10-21T02:00:00.0000000"}, "end": {"dateTime": "2026-10-21T03:00:00.0000000"}, "showAs": "busy"},
        {"start": {"dateTime": "2026-10-21T04:00:00.0000000"}, "end": {"dateTime": "2026-10-21T05:00:00.0000000"}, "showAs": "free"},
        {"start": {"dateTime": "2026-10-21T06:00:00.0000000"}, "end": {"dateTime": "2026-10-21T07:00:00.0000000"}, "isCancelled": True},
    ]}})
    busy = GraphBusy(oauth).busy(at(2026, 10, 21, 8), at(2026, 10, 21, 23))
    assert busy == [(datetime(2026, 10, 21, 2, tzinfo=timezone.utc), datetime(2026, 10, 21, 3, tzinfo=timezone.utc))]


def test_merged_calendar_writes_only_to_primary():
    class Primary:
        created = []

        def available(self):
            return True

        def busy(self, s, e):
            return [(at(2026, 10, 21, 9), at(2026, 10, 21, 10))]

        def own_events(self, s, e):
            return []

        def create(self, title, s, e, d=""):
            self.created.append(title)
            return "p1"

        def delete(self, i):
            pass

    class Extra:
        def available(self):
            return True

        def busy(self, s, e):
            return [(at(2026, 10, 21, 13), at(2026, 10, 21, 14))]

    cal = CalendarWithExtraBusy(Primary(), [Extra()])
    assert len(cal.busy(at(2026, 10, 21, 8), at(2026, 10, 21, 23))) == 2
    assert cal.create("x", at(2026, 10, 21, 11), at(2026, 10, 21, 12)) == "p1"


def test_microsoft_token_requests_carry_scope_google_do_not(tmp_path):
    from muninn.adapters.google.oauth import GoogleOAuth

    seen = {}

    def handler(name):
        def h(request):
            seen[name] = dict(httpx.QueryParams(request.content.decode()))
            return httpx.Response(200, json={"access_token": "a", "refresh_token": "r", "expires_in": 3600})
        return h

    (tmp_path / "c.json").write_text(json.dumps({"client_id": "id", "client_secret": "s"}))
    for name, factory in (("microsoft", MicrosoftOAuth), ("google", GoogleOAuth)):
        c = factory(tmp_path / "c.json", tmp_path / f"{name}.tok", tmp_path / f"{name}.state", "https://x/cb",
                    http=httpx.Client(transport=httpx.MockTransport(handler(name))))
        url = c.authorization_url()
        c.complete("code", dict(httpx.QueryParams(url.split("?", 1)[1]))["state"])
    assert "Mail.Read" in seen["microsoft"]["scope"] and "scope" not in seen["google"]
    assert isinstance(MicrosoftOAuth(tmp_path / "c.json", tmp_path / "t", tmp_path / "s", None), OAuth2Client)
    assert "offline_access" in MICROSOFT.scopes and not any("Send" in s or "Write" in s for s in MICROSOFT.scopes)


def test_work_reply_draft_cannot_be_saved():
    from test_actions import mail_harness, msg, reply

    h, _, work = mail_harness([msg(1, kind="work", subject="Question", body="Can you review?")])
    h.local.reply = reply("needs_reply", company=None, needs=True)
    h.engine.triage_mail()
    [r] = [x for x in h.engine.home_view()["ready"] if x["kind"] == "mail_reply"]
    view = h.engine.prepared_view(r["id"])
    assert view["can_save_draft"] is False and h.cloud.calls == []
    assert h.engine.save_reply_draft(r["id"], "x") is False and work.drafts == []


def test_oauth_callback_route_is_generic(web, tmp_path):
    h, client, devices = web
    assert client.get("/oauth/microsoft/callback?code=x&state=y").status_code == 403  # no such connection in this app
