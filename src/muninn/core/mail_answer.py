"""Answering the user's questions about their mail: finding one, listing or comparing several (bills and their amounts,
dates), what someone wrote.

Answered from the notes Muninn keeps of every mail it has read (who sent it, the subject, its one-sentence summary and
next step), with the full text of the few that match best read again from the mailbox. The notes the model reads are
the ones that share a term with the question (the routing model writes the terms, with the other names the same thing
goes by), most terms matched and newest first; when none does, the latest ones, so the model can still recognise a
sender written another way. The mail is read under its own rule (MailTriager.ask)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Sequence

from .language import Language, written_in
from .models import Observation
from .ports import Clock, Mailbox, ObservationStore
from .privacy import Cleared
from .reply_json import json_object

log = logging.getLogger(__name__)

MATCHES = 40  # notes that share a term with the question, at most
LATEST = 80  # the latest notes read when none does
BODIES = 3  # the best matches whose full text is read again
BODY_CHARS = 4000
KEPT = timedelta(days=3650)  # every note Muninn keeps
FIELDS = ("from", "subject", "summary", "action", "company")  # what a term is looked for in

SYSTEM = """You are Muninn, the user's butler. Answer the user's question about their email, in {language}, briefly.
Reply with one JSON object and nothing else:
{"answer": "<the answer to the user, in {language}>", "mails": ["<the ref of each mail the answer rests on, the most relevant first>"]}
mails are the mails Muninn has read since mail_since, each as a note: who sent it, the subject, a one-sentence summary, the user's next step; the best matches also have their text (body).
Answer only from them. Find, list, sort, add up or compare as the question asks, giving dates and amounts as the mails do.
When matched is false, none of them shared a word with the question: find the ones about it all the same (a sender may be written another way); when none is, say that no mail since mail_since is about it.
When the answer needs what is not here (an attachment, a detail the summary leaves out), say so plainly, and which mail to open to see it."""


def system(words: Language) -> str:
    return written_in(SYSTEM, words)


@dataclass(frozen=True)
class MailAnswer:
    text: str
    mails: tuple[tuple[str, str, str], ...] = ()  # (account, id, thread) of the mails it rests on, most relevant first
    stood_in: bool = False  # a local model answered in place of the cloud


def pick(notes: Sequence[Observation], terms: Sequence[str]) -> tuple[list[Observation], bool]:
    """The notes the question is answered from, and whether they share a term with it: those that do, most terms
    matched and newest first; otherwise the latest."""
    wanted = [t for t in dict.fromkeys(str(t).strip().lower() for t in terms) if t]

    def hits(o: Observation) -> int:
        seen = " ".join(str(o.payload.get(k) or "") for k in FIELDS).lower()
        return sum(t in seen for t in wanted)

    scored = sorted(((hits(o), o) for o in notes), key=lambda x: (x[0], x[1].ts), reverse=True)
    matched = [o for n, o in scored if n][:MATCHES]
    if matched:
        return matched, True
    return sorted(notes, key=lambda o: o.ts, reverse=True)[:LATEST], False


class MailQuestions:
    def __init__(self, store: ObservationStore, clock: Clock, mailboxes: Callable[[], Sequence[Mailbox]],
                 reader: Callable[[], Any], *, words: Language):
        """`mailboxes()`: the mailboxes as they are now (a mail's full text is read again from its own); `reader()`:
        the mail model as it is now (MailTriager, whose `ask` keeps to the mail's rule), None when none is set up."""
        self.words = words
        self._store = store
        self._clock = clock
        self._mailboxes = mailboxes
        self._reader = reader

    def answer(self, question: str, terms: Sequence[str], cleared: Cleared | None) -> MailAnswer | None:
        """The answer, or None when no model answered. `cleared`: the question was found not to be about work."""
        now = self._clock.now()
        notes = [o for o in self._store.observations(now - KEPT, now + timedelta(seconds=1), kinds=("mail",))
                 if o.payload.get("id")]
        if not notes:
            return MailAnswer(self.words("mail.none_read"))
        reader = self._reader()
        if reader is None:
            return None
        picked, matched = pick(notes, terms)
        boxes = {b.name: b for b in self._mailboxes()}
        refs = {f"m{i}": o for i, o in enumerate(picked, 1)}
        mails = []
        for ref, o in refs.items():
            p = o.payload
            mail = {"ref": ref, "date": o.ts.astimezone(now.tzinfo).date().isoformat(),
                    **{k: p[k] for k in ("from", "subject", "category", "summary", "action") if p.get(k)}}
            if matched and len(mails) < BODIES and (body := self._body(boxes.get(str(p.get("account"))), str(p["id"]))):
                mail["body"] = body
            mails.append(mail)
        data = {"today": now.date().isoformat(), "mail_since": min(o.ts for o in notes).astimezone(now.tzinfo).date().isoformat(),
                "matched": matched, "question": question, "mails": mails}
        personal = all(getattr(boxes.get(str(o.payload.get("account"))), "kind", None) == "personal" for o in picked)
        c = reader.ask(system(self.words), data, cleared=cleared, personal=personal)
        d = json_object(c.reply)
        if not d or not str(d.get("answer") or "").strip():
            return None
        rested = [refs[r] for r in dict.fromkeys(str(r) for r in d.get("mails") or []) if r in refs]
        return MailAnswer(str(d["answer"]).strip(),
                          tuple((str(o.payload.get("account")), str(o.payload["id"]), str(o.payload.get("thread") or "")) for o in rested),
                          stood_in=c.stood_in)

    def _body(self, box: Mailbox | None, message_id: str) -> str | None:
        """A mail's text read again from its mailbox; None when it cannot be (the note is what was read of it)."""
        if box is None or not box.available():
            return None
        try:
            m = box.get(message_id)
        except Exception as e:  # noqa: BLE001 - the mailbox's own failure; the question is answered from the note
            log.warning("mail %s not read again from %s: %s", message_id, box.name, e)
            return None
        return m.text[:BODY_CHARS] if m is not None and m.text.strip() else None
