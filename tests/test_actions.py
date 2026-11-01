"""Calendar scheduling, mail triage and Google OAuth."""

from __future__ import annotations

import json

import httpx

from conftest import at, make_harness
from muninn.core.mail import MailTriager
from muninn.core.models import CalendarEvent, MailMessage
from muninn.core.schedule import Task, place

# ── placement ───────────────────────────────────────────────────────────────


def test_place_finds_gaps_with_buffers():
    start, end = at(2026, 10, 22, 9), at(2026, 10, 22, 18)
    busy = [(at(2026, 10, 22, 9, 30), at(2026, 10, 22, 11)), (at(2026, 10, 22, 13), at(2026, 10, 22, 14))]
    blocks = place([Task("a", 60), Task("b", 45), Task("c", 30), Task("d", 30)], busy, start, end)
    assert [(b.task.title, b.start.strftime("%H:%M"), b.end.strftime("%H:%M")) for b in blocks] == [
        ("a", "11:15", "12:15"), ("b", "14:15", "15:00"), ("c", "15:15", "15:45")]  # max 3 blocks


def test_place_skips_what_does_not_fit():
    blocks = place([Task("long", 120)], [], at(2026, 10, 22, 22), at(2026, 10, 22, 23))
    assert blocks == []


# ── calendar actions ────────────────────────────────────────────────────────


class FakeCalendar:
    def __init__(self, busy=()):
        self._busy = list(busy)
        self.made: dict[str, CalendarEvent] = {}
        self.theirs: list[CalendarEvent] = []
        self.n = 0

    def available(self):
        return True

    def busy(self, start, end):
        return self._busy + [(e.start, e.end) for e in self.made.values()]

    def own_events(self, start, end):
        return [e for e in self.made.values() if e.end > start and e.start < end]

    def events(self, start, end):  # the user's other calendars
        return [e for e in self.theirs if e.end > start and e.start < end]

    def create(self, title, start, end, description=""):
        self.n += 1
        eid = f"e{self.n}"
        self.made[eid] = CalendarEvent(eid, start, end, title)
        return eid

    def delete(self, event_id):
        self.made.pop(event_id, None)


def test_schedule_today_once_undo_and_replan():
    h = make_harness(at(2026, 10, 22, 9))
    cal = FakeCalendar(busy=[(at(2026, 10, 22, 10), at(2026, 10, 22, 12))])
    h.engine.calendar = cal
    acts = h.engine.schedule_today()
    assert acts and all(e.title.startswith("Muninn · ") for e in cal.made.values())
    assert all(not (e.start < at(2026, 10, 22, 12, 10) and e.end > at(2026, 10, 22, 9, 50)) for e in cal.made.values())
    assert h.engine.schedule_today() == []  # once a day
    first = h.engine.home_view()["done"][0]
    assert first["text"].startswith("排進行事曆")
    before = len(cal.made)
    assert h.engine.undo_action(first["id"]) and len(cal.made) == before - 1
    h.clock.t = at(2026, 10, 22, 15)
    h.engine.schedule_today(force=True)  # not-started blocks are replaced, not duplicated
    assert all(e.start >= at(2026, 10, 22, 15) or e.end <= at(2026, 10, 22, 15) for e in cal.made.values())


# ── mail ────────────────────────────────────────────────────────────────────


class FakeMailbox:
    def __init__(self, name, kind, messages):
        self.name, self.kind, self.messages, self.drafts = name, kind, messages, []

    def available(self):
        return True

    def fetch(self, cursor, now):
        new = [m for m in self.messages if cursor is None or m.ts.timestamp() > cursor]
        return new, (max(m.ts.timestamp() for m in self.messages) if self.messages else cursor)

    def cursor_of(self, m):
        return m.ts.timestamp()

    def create_draft(self, reply_to, body):
        self.drafts.append((reply_to.thread_id, body))
        return "d1"

    def get(self, message_id):
        return next((m for m in self.messages if m.id == message_id), None)


def msg(i, kind="personal", subject="Thanks for applying", body="We received your application to Acme."):
    return MailMessage(f"m{i}", f"t{i}", "personal" if kind == "personal" else "work", kind, "Acme Talent <jobs@acme.example>",
                       "me@example.com", subject, at(2026, 10, 22, 9, i), body, f"<m{i}@acme>")


def reply(category, company="Acme", needs=False):
    return json.dumps({"category": category, "company": company, "summary": "摘要", "action": "下一步", "needs_reply": needs,
                       "draft": "Thanks! [time]" if needs else None})


def mail_harness(messages, cloud_mail=True):
    h = make_harness(at(2026, 10, 22, 10))
    personal = FakeMailbox("personal", "personal", [m for m in messages if m.account_kind == "personal"])
    work = FakeMailbox("work", "work", [m for m in messages if m.account_kind == "work"])
    h.engine.mailboxes = [personal, work]
    h.engine.triager = MailTriager(h.engine.router, cloud_mail=cloud_mail)
    return h, personal, work


def test_personal_mail_to_cloud_work_mail_local_only():
    secret = "WORK-SECRET-55"
    h, _, _ = mail_harness([msg(1), msg(2, kind="work", body=f"internal {secret}")])
    h.cloud.reply = reply("fyi", company=None)
    h.local.reply = reply("fyi", company=None)
    assert h.engine.triage_mail() == 2
    assert len(h.cloud.calls) == 1 and secret not in h.cloud.calls[0][1]
    assert any(secret in p for _, p in h.local.calls)
    assert json.loads(h.cloud.calls[0][1])["subject"] == "Thanks for applying"


def test_cloud_mail_off_keeps_personal_mail_local():
    h, _, _ = mail_harness([msg(1)], cloud_mail=False)
    h.local.reply = reply("fyi", company=None)
    h.engine.triage_mail()
    assert h.cloud.calls == [] and len(h.local.calls) == 1


def test_confirmation_sets_applied_with_undo():
    h, _, _ = mail_harness([msg(1)])
    h.cloud.reply = reply("application_confirmation")
    h.engine.triage_mail()
    assert h.plans.plans["jobs"].tracker_item("acme").status == "applied"
    action = next(d for d in h.engine.home_view()["done"] if d["text"].startswith("收到"))
    assert h.engine.undo_action(action["id"])
    assert h.plans.plans["jobs"].tracker_item("acme").status == "todo"
    assert h.engine.triage_mail() == 0  # cursor moved: not read twice


def test_interview_asks_first():
    h, _, _ = mail_harness([msg(1, subject="Interview invitation")])
    h.cloud.reply = reply("interview")
    h.engine.triage_mail()
    assert h.plans.plans["jobs"].tracker_item("acme").status == "todo"  # nothing changed yet
    [q] = [c for c in h.engine.pending_confirmations() if c.kind == "action"]
    assert "面試邀請" in q.question and "狀態改成面試" in q.question  # what it is based on, and what it would do, in words
    assert "interview" not in q.question
    h.engine.answer(q.id, q.options[0])
    assert h.plans.plans["jobs"].tracker_item("acme").status == "interview"
    done = next(d for d in h.engine.home_view()["done"] if "狀態改成面試" in d["text"])
    assert done["undoable"]


def test_a_mail_with_something_to_do_by_a_date_is_a_to_do_that_opens_the_mail():
    h, _, _ = mail_harness([msg(1, subject="Payment failed", body="Update your card by Oct 25.")])
    h.cloud.reply = json.dumps({"category": "deadline", "company": None, "summary": "付款失敗", "action": "更新信用卡",
                                "due": "2026-10-25", "needs_reply": False, "draft": None})
    h.engine.triage_mail()
    [t] = [t for t in h.engine.home_view()["tasks"] if t["title"] == "更新信用卡"]
    assert t["due"] == "2026-10-25" and t["mail"] == "personal:m1" and "付款失敗" in t["context"]
    assert h.channel.sent  # still said at once


def test_a_mail_handed_over_again_is_read_once():
    h, personal, _ = mail_harness([msg(1, subject="Payment failed", body="Update your card by Oct 25.")])
    h.cloud.reply = json.dumps({"category": "deadline", "company": None, "summary": "付款失敗", "action": "更新信用卡",
                                "due": "2026-10-25", "needs_reply": False, "draft": None})
    personal.fetch = lambda cursor, now: (list(personal.messages), personal.messages[-1].ts.timestamp())  # an `after:` that keeps the newest
    for _ in range(3):
        h.engine.triage_mail()
    assert len(h.cloud.calls) == 1
    assert [t["title"] for t in h.engine.home_view()["tasks"]].count("更新信用卡") == 1


def test_a_run_that_stops_part_way_keeps_the_cursor_its_mailbox_reads():
    from muninn.adapters.microsoft.graph import GraphMailbox

    class OAuth:
        authorized = True

        def get(self, url, headers=None, **params):
            self.filter = params["$filter"]
            return {"value": [{"id": f"AAMk{i}", "conversationId": "c", "from": {"emailAddress": {"name": "A", "address": "a@corp.example"}},
                               "toRecipients": [], "subject": f"s{i}", "receivedDateTime": f"2026-10-22T0{i}:00:00Z",
                               "body": {"contentType": "text", "content": "fyi"}, "internetMessageId": f"<{i}@corp>", "webLink": ""}
                              for i in range(1, 4)]}

    h, _, _ = mail_harness([])
    oauth = OAuth()
    box = GraphMailbox(oauth)
    h.engine.mailboxes = [box]
    h.local.reply = h.cloud.reply = reply("fyi", company=None)
    h.engine.mailroom.triage(limit=2)  # three new, two read: the rest next run
    assert h.store.get_state(f"cursor:mail:{box.name}") == "2026-10-22T02:00:00Z"
    h.engine.mailroom.triage(limit=2)
    assert oauth.filter == "receivedDateTime gt 2026-10-22T02:00:00Z"  # a filter the mailbox takes


def test_reply_draft_is_saved_never_sent():
    h, personal, _ = mail_harness([msg(1, subject="Quick question", body="Can we talk Thursday?")])
    h.cloud.reply = reply("needs_reply", company=None, needs=True)
    h.engine.triage_mail()
    [r] = [x for x in h.engine.home_view()["ready"] if x["kind"] == "mail_reply"]
    view = h.engine.prepared_view(r["id"])
    assert view["draft"] == "Thanks! [time]" and view["url"].endswith("#all/t1")
    assert h.engine.save_reply_draft(r["id"], "Edited reply")
    assert personal.drafts == [("t1", "Edited reply")]
    assert not hasattr(personal, "send")
    assert h.engine.save_reply_draft(r["id"], "again") is False


def test_a_model_answering_nonsense_to_every_mail_is_not_taken_out_on_the_mail():
    h, _, _ = mail_harness([msg(1), msg(2), msg(3)])
    h.cloud.reply = "nonsense"
    h.local.reply = "nonsense"
    assert h.engine.triage_mail() == 1  # the first left as a to-do; the second no better: the model's doing, wait
    assert len(h.engine.tasks.open()) == 1
    h.cloud.reply = reply("fyi", company=None)
    assert h.engine.triage_mail() == 2 and len(h.engine.tasks.open()) == 1


# ── OAuth ───────────────────────────────────────────────────────────────────


def test_oauth_state_and_token_refresh(tmp_path):
    from muninn.adapters.google.oauth import GoogleAuthError, GoogleOAuth

    (tmp_path / "client.json").write_text(json.dumps({"web": {"client_id": "cid", "client_secret": "cs"}}))
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(httpx.QueryParams(request.content.decode())))
        if calls[-1].get("grant_type") == "authorization_code":
            return httpx.Response(200, json={"access_token": "a1", "refresh_token": "r1", "expires_in": 3600})
        return httpx.Response(200, json={"access_token": "a2", "expires_in": 3600})

    g = GoogleOAuth(tmp_path / "client.json", tmp_path / "tok.json", tmp_path / "state.json", "https://x/oauth/google/callback",
                    http=httpx.Client(transport=httpx.MockTransport(handler)))
    url = g.authorization_url()
    state = dict(httpx.QueryParams(url.split("?", 1)[1]))["state"]
    try:
        g.complete("code", "wrong")
        raise AssertionError("expected a state error")
    except GoogleAuthError:
        pass
    url = g.authorization_url()
    state = dict(httpx.QueryParams(url.split("?", 1)[1]))["state"]
    g.complete("code", state)
    assert g.authorized and g.access_token() == "a1"
    assert oct((tmp_path / "tok.json").stat().st_mode & 0o777) == "0o600"
    tok = json.loads((tmp_path / "tok.json").read_text())
    tok["expires_at"] = 0
    (tmp_path / "tok.json").write_text(json.dumps(tok))
    assert g.access_token() == "a2" and calls[-1]["grant_type"] == "refresh_token"
    try:
        g.complete("code", state)  # single use
        raise AssertionError("expected reuse to fail")
    except GoogleAuthError:
        pass


def test_gmail_body_text():
    import base64

    from muninn.adapters.google.gmail import body_text

    enc = lambda s: base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")  # noqa: E731
    assert body_text({"mimeType": "multipart/alternative", "parts": [
        {"mimeType": "text/plain", "body": {"data": enc("hello plain")}},
        {"mimeType": "text/html", "body": {"data": enc("<p>hello html</p>")}}]}) == "hello plain"
    assert body_text({"mimeType": "text/html", "body": {"data": enc("<p>only <b>html</b></p>")}}).strip() == "only html"


def test_expired_google_grant_asks_to_relink(tmp_path):
    from muninn.adapters.google.oauth import GoogleAuthError, GoogleOAuth

    (tmp_path / "client.json").write_text(json.dumps({"web": {"client_id": "cid", "client_secret": "cs"}}))
    (tmp_path / "tok.json").write_text(json.dumps({"access_token": "a", "refresh_token": "r", "expires_at": 0}))
    g = GoogleOAuth(tmp_path / "client.json", tmp_path / "tok.json", tmp_path / "state.json", "https://x/cb",
                    http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))))
    assert g.status() == "ok"
    try:
        g.access_token()
        raise AssertionError("expected failure")
    except GoogleAuthError:
        pass
    assert g.status() == "relink" and not g.authorized

    h = make_harness(at(2026, 10, 22, 10))
    h.engine.connections = [g]
    assert h.engine.home_view()["relink"] == ["google"]
    assert len(h.engine.remind_relink()) == 1 and h.engine.remind_relink() == []  # once a day
    assert len(h.channel.sent) == 1


def test_urgent_mail_pushes_once():
    h, _, _ = mail_harness([msg(1, subject="Payment failed", body="Your card payment failed, pay by Friday.")])
    h.cloud.reply = reply("deadline", company=None)
    h.engine.triage_mail()
    assert [[m.text for m in b] for b in h.channel.sent] == [["摘要"]]
    [alert] = [i for i in h.store.inbox() if i.kind == "alert"]
    assert alert.signal_key == "mail:personal:m1"


def test_fyi_mail_does_not_push():
    h, _, _ = mail_harness([msg(1)])
    h.cloud.reply = reply("fyi", company=None)
    h.engine.triage_mail()
    assert h.channel.sent == []


def test_read_mail_in_app():
    h, _, _ = mail_harness([msg(1, body="Full body text")])
    h.cloud.reply = reply("fyi", company=None)
    h.engine.triage_mail()
    [m] = h.engine.home_view()["mail"]
    full = h.engine.read_mail(m["account"], m["id"])
    assert full["text"] == "Full body text" and full["summary"] == "摘要" and full["category"] == "fyi"
    assert h.engine.read_mail("personal", "nope") is None


def test_home_shows_one_alert_per_topic_and_none_for_removed_goals():
    from muninn.core.models import InboxItem

    h = make_harness(at(2026, 10, 21, 1))
    for n in range(3):
        h.store.add_inbox(InboxItem(None, at(2026, 10, 21, 0, 10 * n), "health", "alert", f"23:30 之後不工作:已 {n * 30} 分鐘", "",
                                    signal_key="health:goal:no-late-work"))
    h.store.add_inbox(InboxItem(None, at(2026, 10, 21, 0), "jobs", "alert", "補 Rust", "", signal_key="jobs:goal:rust"))
    alerts = h.engine.views().alerts()
    late = [a for a in alerts if a["key"] == "health:goal:no-late-work"]
    assert len(late) == 1 and late[0]["times"] == 3 and late[0]["title"].endswith("60 分鐘")
    assert not any(a["key"] == "jobs:goal:rust" for a in alerts)  # no such goal any more


def test_conversations_and_the_evidence_see_the_users_own_calendar():
    import json as _json


    h = make_harness(at(2026, 10, 22, 15))
    cal = FakeCalendar()
    cal.theirs = [CalendarEvent("x1", at(2026, 10, 22, 10), at(2026, 10, 22, 11), "Meet the tiler about the bathroom", "Personal")]
    h.engine.calendar = cal
    h.local.reply = _json.dumps({"actions": [], "reply": "好"})
    h.engine.understand_command("明天有空嗎")
    sent = _json.loads(h.local.calls[0][1])
    assert sent["context"]["calendar"][0]["title"] == "Meet the tiler about the bathroom"
    h.engine.messages.receive([{"id": "m1", "project": "home", "type": "task", "text": "Talk to the tiler"}])
    h.local.reply = _json.dumps({"done": [{"task": "m1", "why": "10:00 跟磁磚師傅見過面"}]})
    assert h.engine.completion_detector().run() == 1
    assert any(e["kind"] == "meeting" for e in _json.loads(h.local.calls[-1][1])["evidence"])


def test_a_mail_no_answer_can_be_read_for_is_left_as_a_to_do_and_the_rest_go_on():
    h, personal, _ = mail_harness([msg(1, subject="Garbled newsletter"), msg(2, subject="Thanks for applying")])
    answers = {"m1": "這封我看不太懂", "m2": reply("fyi", company=None)}

    def complete(system, prompt, *, json_mode=False):
        return next(v for k, v in answers.items() if ("Garbled" in prompt) == (k == "m1"))

    h.cloud.complete = complete
    assert h.engine.triage_mail() == 2  # not held up behind it
    [todo] = h.engine.tasks.open()
    assert todo["title"] == "讀這封信:Garbled newsletter" and "讀不出來" in todo["context"]
    notes = h.store.observations(at(2026, 10, 22, 0), kinds=("mail",))
    assert [n.payload["id"] for n in notes] == ["m2"]
    assert h.engine.triage_mail() == 0 and len(h.engine.tasks.open()) == 1  # past it for good


def test_no_model_answering_is_not_held_against_the_mail():
    h, _, _ = mail_harness([msg(1)])
    h.cloud.up = h.local.up = False  # nothing answers
    for _ in range(5):
        assert h.engine.triage_mail() == 0
    assert h.channel.sent == [] and not [i for i in h.store.inbox() if i.kind == "alert"]
    h.cloud.up = True
    h.cloud.reply = reply("fyi", company=None)
    assert h.engine.triage_mail() == 1  # read once a model answers again


def test_a_mail_that_did_not_come_through_as_text_is_a_to_do_without_asking_a_model():
    blank = msg(1, subject="Photos from Saturday", body="  ")
    garbled = MailMessage("m2", "t2", "personal", "personal", "Billing <billing@shop.example>", "me@example.com",
                          "您的帳單", at(2026, 10, 22, 9, 2), "\ufffd" * 40 + "ab", "<m2@shop>")
    h, _, _ = mail_harness([blank, garbled])
    assert h.engine.triage_mail() == 2 and h.cloud.calls == [] and h.local.calls == []  # no model reads what is not text
    todos = {t["title"]: t for t in h.engine.tasks.open()}
    assert "我猜:內文是空的" in todos["讀這封信:Photos from Saturday"]["context"]
    assert "亂碼" in todos["讀這封信:您的帳單"]["context"]
    assert not [x for x in h.engine.home_view()["ready"] if x["kind"] == "mail_reply"]  # nothing goes back to the sender


def test_a_mail_whose_meaning_is_unclear_is_a_to_do_with_a_guess():
    h, _, _ = mail_harness([msg(1, subject="re: the thing", body="ok so about the thing, can we do the other one instead?")])
    h.cloud.reply = json.dumps({"category": "unclear", "guess": "可能是在改約"})
    assert h.engine.triage_mail() == 1
    [todo] = h.engine.tasks.open()
    assert todo["links"] == ["mail:personal:m1"]  # opens the mail
    assert todo["title"] == "讀這封信:re: the thing" and "看不懂它要什麼" in todo["context"] and "可能是在改約" in todo["context"]
    assert not [x for x in h.engine.home_view()["ready"] if x["kind"] == "mail_reply"]
    assert not h.store.observations(at(2026, 10, 22, 0), kinds=("mail",))  # nothing is noted as understood
