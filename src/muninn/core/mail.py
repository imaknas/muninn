"""Reading new mail: what each message is, what to do about it, and a reply
draft when one is needed.

Personal mail may go to the cloud model when the user allowed it
(cloud_mail); work mail is sensitive: the local model reads it, unless
routing sends everything to the cloud. Muninn
never sends: a reply becomes a draft the user reviews and sends."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol, Sequence

from .actions import ActionGate, DecidedBy, TrackerStatus
from .llm_router import Completion, LLMRequest, LLMRouter
from .models import ActionRecord, DomainPlan, MailMessage, Observation, Prepared, Sensitivity, Signal
from .ports import ActionLog, Clock, Mailbox, MarkerStore, ObservationStore, PreparedStore, StateStore
from .privacy import Cleared, mail_notes_payload, mail_payload
from .language import Language, written_in
from .reply_json import json_object

log = logging.getLogger(__name__)

CATEGORIES = ["application_confirmation", "interview", "rejection", "offer", "needs_reply", "deadline", "fyi", "promo", "unclear"]

MAIL_SYSTEM = """You are the user's assistant reading one email for them.
Reply with one JSON object and nothing else:
{"category": "<one of {categories}>",
 "company": "<the hiring company if this is about a job application, else null>",
 "summary": "<one sentence, in {language}>",
 "action": "<the user's next step: one short sentence in {language}, or null if none>",
 "due": "<the date that step must be done by, YYYY-MM-DD, if the mail gives one; else null>",
 "needs_reply": true|false,
 "draft": "<if needs_reply: a reply in the email's language, ready to send, signed with no name; else null>",
 "guess": "<if unclear: your best guess at what it is and what the sender wants, in {language}; else null>"}
Categories: application_confirmation = an automatic "we received your application"; interview = an invitation or scheduling request; offer; rejection; deadline = something the user must pay, submit or do by a date (bills, failed payments, forms); needs_reply = a person the user already deals with (a thread the user took part in, a recruiter for a job the user applied to, family, colleagues) asks the user something; fyi = worth knowing, nothing left to do later (a one-time login code or link is used the moment the user asked for it); promo = marketing, newsletters, and cold outreach from people or organizations the user has no relationship with (recruiting pitches, sales, invitations to programs); unclear = you can read the words but cannot tell what it is about or what the sender wants.
A draft only says what the user has already decided; anything else (interest, yes or no, dates, money) is a [placeholder] for them to fill in."""


def mail_system(words: Language) -> str:
    return written_in(MAIL_SYSTEM, words).replace("{categories}", str(CATEGORIES))


def parse_triage(reply: str | None) -> dict[str, Any] | None:
    d = json_object(reply)
    if d is None or d.get("category") not in CATEGORIES:
        return None
    if d["category"] == "unclear":
        return {"category": "unclear", "guess": str(d.get("guess") or "")[:300] or None}
    needs = bool(d.get("needs_reply")) and bool(d.get("draft"))
    return {
        "category": d["category"],
        "company": str(d["company"])[:80] if d.get("company") else None,
        "summary": str(d.get("summary") or "")[:300],
        "action": str(d["action"])[:300] if d.get("action") else None,
        "due": str(d["due"])[:10] if d.get("due") else None,  # a date or nothing, as the task keeps it
        "needs_reply": needs,
        "draft": str(d["draft"])[:4000] if needs else None,
    }


class MailTriager:
    def __init__(self, router: LLMRouter, *, cloud_mail: bool):
        self._router = router
        self._cloud = cloud_mail

    def triage(self, m: MailMessage, *, words: Language) -> tuple[dict[str, Any] | None, str | None]:
        """The triage result and which backend produced it; what the user reads of it in `words`' language."""
        system = mail_system(words)
        content = mail_payload(m) if (self._cloud and m.account_kind == "personal") else None
        if content is not None:
            req = LLMRequest(system, content, Sensitivity.PERSONAL, "mail", json_mode=True, task="mail_triage")
        else:
            text = json.dumps({"from": m.sender, "subject": m.subject, "date": m.ts.isoformat(), "body": m.text}, ensure_ascii=False)
            req = LLMRequest(system, text, Sensitivity.SENSITIVE, None, json_mode=True, task="mail_triage")
        reply, backend = self._router.complete_with_backend(req)
        return parse_triage(reply), backend

    def ask(self, system: str, data: dict[str, Any], *, cleared: Cleared | None, personal: bool) -> Completion:
        """A question about mail already read (mail_answer.py), under the same rule as reading it: the cloud only for
        personal mail when cloud_mail allows it, and only when the question itself was cleared; otherwise sensitive."""
        content = mail_notes_payload(data, cleared, personal) if self._cloud else None
        if content is not None:
            req = LLMRequest(system, content, Sensitivity.PERSONAL, "mail", json_mode=True, task="mail_answer")
        else:
            req = LLMRequest(system, json.dumps(data, ensure_ascii=False, default=str), Sensitivity.SENSITIVE, None,
                             json_mode=True, task="mail_answer")
        return self._router.complete_case(req)


def unreadable(m: MailMessage) -> str | None:
    """Why a mail cannot be read as text at all, before any model is asked: "empty" (nothing but pictures or
    attachments came through), "garbled" (its text did not decode); None when it can be read."""
    text = m.text.strip()
    if not text:
        return "empty"
    bad = sum(1 for c in text if c == "\ufffd" or (ord(c) < 32 and c not in "\n\r\t"))
    return "garbled" if bad >= max(5, len(text) // 5) else None


def mail_link(m: MailMessage) -> str:
    """How a to-do points at the mail it came from (opened in the app's reader)."""
    return f"mail:{m.account}:{m.id}"


URGENT_MAIL = {"offer": 1, "interview": 1, "deadline": 2}


class MailStorage(StateStore, ObservationStore, PreparedStore, ActionLog, MarkerStore, Protocol):
    """Each mailbox's cursor, the mail notes kept as observations, reply drafts prepared, drafts saved, and a marker
    for each message read (a mailbox may hand the same one over again)."""


class Mailroom:
    """New mail read and acted on, a message opened in the app, a reply saved as a draft. Never sends."""

    def __init__(self, store: MailStorage, clock: Clock, gate: ActionGate, mailboxes: Sequence[Mailbox],
                 triager: MailTriager | None, plans: Callable[[], Sequence[DomainPlan]],
                 ingest: Callable[[list[Observation]], Any], alert: Callable[[Signal, datetime], None],
                 todo: Callable[..., Any], *, words: Language):
        """`ingest` stores (and attributes) observations; `alert` composes and pushes one signal; `todo(title,
        context, due=, links=)`: a to-do kept for the user."""
        self.words = words
        self._store = store
        self._clock = clock
        self._gate = gate
        self.mailboxes = list(mailboxes)
        self.triager = triager
        self._plans = plans
        self._ingest = ingest
        self._alert = alert
        self._todo = todo

    def _match_target(self, company: str | None, text: str) -> tuple[DomainPlan, str] | None:
        if not company:
            return None
        c = company.lower()
        for plan in self._plans():
            for t in plan.targets:
                name = t.title.lower()
                if c in name or name.split(" ")[0] in c:
                    if plan.tracker_item(t.id):
                        return plan, t.id
        return None

    def triage(self, limit: int = 10) -> int:
        """Reads new mail. Confirmations of an application set its status
        (undoable); interviews, offers and rejections ask first; messages that
        need an answer get a reply draft ready for review. Never sends."""
        if self.triager is None:
            return 0
        now = self._clock.now()
        done = 0
        for box in self.mailboxes:
            if done >= limit or not box.available():
                continue
            key = f"cursor:mail:{box.name}"
            messages, cursor = box.fetch(self._store.get_state(key), now)
            handled, garbled = 0, False
            for m in messages:
                if done >= limit:
                    break
                read = f"mail:{m.account}:{m.id}"
                if self._store.claimed(read):  # handed over again (a cursor is only as fine as its mailbox's): read once
                    handled += 1
                    continue
                why = unreadable(m)
                if why is not None:  # no model can read what did not come through
                    self._unclear(m, "format", {"empty": self.words("mail.format_empty"),
                                                 "garbled": self.words("mail.format_garbled")}[why])
                    self._store.claim(read, now)
                    handled += 1
                    done += 1
                    continue
                result, backend = self.triager.triage(m, words=self.words)
                if result is None and backend is not None:
                    result, backend = self.triager.triage(m, words=self.words)  # an answer that could not be read: asked once more
                if result is None and (backend is None or garbled):
                    break  # no model answered, or the last mail's answer was no better: the model's doing; from here next run
                garbled = result is None
                if result is None:  # answered, but nothing could be read from it: the user's to read, the rest go on
                    self._unclear(m, "answer", None)
                elif result["category"] == "unclear":
                    self._unclear(m, "meaning", result["guess"])
                else:
                    self._apply(m, result, backend, now)
                self._store.claim(read, now)
                handled += 1
                done += 1
            if handled == len(messages):
                self._store.set_state(key, cursor)
            elif handled:
                self._store.set_state(key, box.cursor_of(messages[handled - 1]))
        return done

    def _unclear(self, m: MailMessage, what: str, guess: str | None) -> None:
        """A mail that could not be understood (its "format": it did not come through as text; its "meaning": read,
        but what it is or wants is not clear; or the "answer" about it could not be read): a to-do for the user to
        read it, with Muninn's guess. Nothing goes back to the sender, and the mail after it is read as usual."""
        words = self.words
        said = {"format": words("mail.no_content"), "meaning": words("mail.no_meaning"), "answer": words("mail.unreadable_answer")}[what]
        guessed = words("mail.guessed", guess=guess) if guess else ""
        where = words("mail.where_web", url=m.web_url) if m.web_url else words("mail.where_box", account=m.account)
        self._todo(words("mail.read_this", subject=m.subject or words("mail.no_subject")),
                   words("mail.unclear", said=said, sender=m.sender, guessed=guessed, where=where), links=(mail_link(m),))

    def _apply(self, m: MailMessage, r: dict[str, Any], backend: str | None, now: datetime) -> None:
        sens = Sensitivity.PERSONAL if m.account_kind == "personal" else Sensitivity.SENSITIVE
        payload = {"account": m.account, "id": m.id, "thread": m.thread_id, "from": m.sender, "subject": m.subject,
                   "category": r["category"], "summary": r["summary"], "action": r["action"], "sender": m.sender}
        self._ingest([Observation("mail", m.ts, "mail", f"mail:{m.account}:{m.id}", payload, None, None, sens)])
        match = self._match_target(r["company"], m.subject)
        if match:
            plan, target = match
            item = plan.tracker_item(target)
            title = plan.target_title(target)
            if r["category"] == "application_confirmation" and item and item.status == "todo":
                # a confirmation mail says it plainly: done, undoable
                self._gate.run(TrackerStatus(plan.id, target, "applied", self.words("mail.confirmed", target=title)), DecidedBy.INFERRED_SURE)
            elif r["category"] in ("interview", "offer", "rejection"):
                status = {"interview": "interview", "offer": "offer", "rejection": "rejected"}[r["category"]]
                said = {"interview": self.words("mail.sent_interview", target=title), "offer": self.words("mail.sent_offer", target=title),
                        "rejection": self.words("mail.sent_rejection", target=title)}[r["category"]]
                if item and item.status != status:  # read from a mail by a model: asked first
                    self._gate.run(TrackerStatus(plan.id, target, status, said), DecidedBy.INFERRED_UNSURE)
        if r["category"] in URGENT_MAIL:  # something to do: kept as a to-do, and said at once
            self._todo(r["action"] or r["summary"] or m.subject or self.words("mail.no_subject"),
                       self.words("mail.summary_from", summary=r["summary"], sender=m.sender) if r["summary"]
                       else self.words("mail.from", sender=m.sender),
                       due=r["due"], links=(mail_link(m),))
            self._alert(Signal(
                key=f"mail:{m.account}:{m.id}", domain="mail", kind="mail", priority=1, level=1,
                title=r["summary"] or m.subject, actions=(r["action"],) if r["action"] else (),
                sensitivity=sens, domain_priority=URGENT_MAIL[r["category"]],
            ), now)
        if r["needs_reply"] and r["draft"]:
            self._store.add_prepared(Prepared(
                None, now, "mail", m.thread_id, "mail_reply", m.subject or self.words("mail.no_subject"),
                {"from": m.sender, "summary": r["summary"], "action": r["action"], "draft": r["draft"],
                 "can_save_draft": m.account_kind == "personal", "web_url": m.web_url,
                 "mail": {"id": m.id, "thread_id": m.thread_id, "account": m.account, "account_kind": m.account_kind,
                          "sender": m.sender, "to": m.to, "subject": m.subject, "ts": m.ts.isoformat(), "message_id": m.message_id}},
                prepared_by=backend or ""))

    def read(self, account: str, message_id: str) -> dict[str, Any] | None:
        """The full message for the in-app reader, with Muninn's notes on it."""
        box = next((b for b in self.mailboxes if b.name == account), None)
        m = box.get(message_id) if box else None
        if m is None:
            return None
        obs = self._store.observations(m.ts - timedelta(seconds=1), m.ts + timedelta(seconds=1), kinds=("mail",))
        note = next((o.payload for o in obs if o.payload.get("id") == message_id), {})
        return {"account": account, "id": m.id, "thread": m.thread_id, "from": m.sender, "to": m.to,
                "subject": m.subject, "ts": m.ts.isoformat(), "text": m.text, "web_url": m.web_url, "kind": m.account_kind,
                "category": note.get("category"), "summary": note.get("summary"), "action": note.get("action")}

    def save_reply_draft(self, pid: int, body: str) -> bool:
        """Saves the (possibly edited) reply as a draft in the mailbox."""
        p = self._store.get_prepared(pid)
        if p is None or p.kind != "mail_reply" or p.status != "ready" or p.data.get("can_save_draft") is False:
            return False
        mm = p.data["mail"]
        box = next((b for b in self.mailboxes if b.name == mm["account"]), None)
        if box is None:
            return False
        msg = MailMessage(mm["id"], mm["thread_id"], mm["account"], mm["account_kind"], mm["sender"], mm["to"],
                          mm["subject"], datetime.fromisoformat(mm["ts"]), "", mm.get("message_id", ""))
        box.create_draft(msg, body)
        self._store.set_prepared_status(pid, "done")
        self._store.log_action(ActionRecord(None, self._clock.now(), "mail", self.words("mail.draft_saved", title=p.title), {}))
        return True

    def tools(self) -> list[Any]:
        """Questions about the mail read: finding one, listing or comparing several."""
        from .mail_answer import MailQuestions
        from .tools.conversation import FindMail

        return [FindMail(MailQuestions(self._store, self._clock, lambda: self.mailboxes, lambda: self.triager, words=self.words),
                         self._store)]

    def jobs(self) -> list[Any]:
        from .actors import Job

        return [Job("mail triage", self.triage, phase="late")]
