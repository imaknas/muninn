"""An English Muninn's to-dos from mail, what a reported fact did, and its weekly reflection are in English."""

from __future__ import annotations

import json
import re

from conftest import at, make_harness
from muninn.core.mail import MailTriager
from muninn.core.models import ExternalTodo, Turn
from muninn.core.reflect import Reflector
from test_actions import FakeMailbox, msg


def no_chinese(text: str) -> bool:
    return re.search(r"[一-鿿]", text) is None


def mail_harness(messages):
    h = make_harness(at(2026, 10, 22, 10), lang="en")
    h.engine.mailboxes = [FakeMailbox("personal", "personal", messages)]
    h.engine.triager = MailTriager(h.engine.router, cloud_mail=True)
    return h


def test_a_mail_that_cannot_be_read_or_understood_is_an_english_to_do():
    h = mail_harness([msg(1, subject="Photos from Saturday", body="  "),
                      msg(2, subject="re: the thing", body="ok so about the thing, can we do the other one instead?")])
    h.cloud.reply = json.dumps({"category": "unclear", "guess": "they may want to move the date"})
    assert h.engine.triage_mail() == 2
    todos = {t["title"]: t["context"] for t in h.engine.tasks.open()}
    assert todos["Read this mail: Photos from Saturday"].startswith(
        "Muninn can't see what it says. From: Acme Talent <jobs@acme.example>. My guess: the body is empty")
    assert "can't tell what it wants" in todos["Read this mail: re: the thing"]
    assert "My guess: they may want to move the date. It's in the personal mailbox." in todos["Read this mail: re: the thing"]
    assert all(no_chinese(k + v) for k, v in todos.items())


def test_a_confirmation_mail_sets_the_status_in_english():
    h = mail_harness([msg(1)])
    h.cloud.reply = json.dumps({"category": "application_confirmation", "company": "Acme", "summary": "They got it",
                                "action": None, "needs_reply": False, "draft": None})
    h.engine.triage_mail()
    texts = [a.text for a in h.store.actions(at(2026, 10, 22, 0))]
    assert any(t.startswith("Got the confirmation from Acme") for t in texts), texts


class Records:
    def projects(self):
        return {"home-budget"}

    def todos(self):
        return [ExternalTodo("home-budget", "me", "Ask the garage for the delivery date")]

    def state(self, project):
        return None

    def weekly_stats(self, w):
        return None


def test_what_a_reported_fact_did_is_said_in_english():
    h = make_harness(at(2026, 10, 21, 21), lang="en")
    h.engine.records = Records()
    replies = [json.dumps({"actions": [{"tool": "record_fact", "args": {"fact": "The car comes in March"}}], "reply": "Noted"}),
               json.dumps({"domain": "jobs", "summary": "The car is expected in March",
                           "effects": [{"id": "deadline:jobs/call-bank", "effect": "resolves", "why": "they already asked"},
                                       {"id": "todo:home-budget:0", "effect": "resolves", "why": "the date is known"}],
                           "projects": ["home-budget"]})]
    h.local.complete = lambda system, prompt, *, json_mode=False: replies.pop(0)
    r = h.engine.command("The car comes in March")
    texts = [o.text for o in r.outcomes]
    assert "Marked done: Call the bank (they already asked)" in texts
    assert "home-budget's to-do “Ask the garage for the delivery date” may be settled: the date is known" in texts


def test_the_weekly_reflection_is_titled_in_english():
    h = make_harness(at(2026, 10, 25, 21), lang="en")
    h.local.reply = json.dumps({"summary": "Wants to apply", "domain": "jobs", "commitments": [], "decisions": [], "themes": ["jobs"],
                                "mood": None, "friction": []})
    h.engine.reflector = Reflector(h.store, h.engine.router, h.clock)
    t = at(2026, 10, 22, 10)
    h.store.add_turns([Turn("s1:1", "s1", t, "user", "resume", "a")])
    h.engine.reflector.digest(h.engine.domains(), words=h.engine.words)
    h.local.reply = "A reflection."
    [p] = h.engine.reflection(preview=True)
    assert p.title == "Weekly reflection, Oct 19–Oct 25 (preview)"
    h.clock.t = at(2026, 10, 26, 8, 5)
    [item] = h.engine.reflection()
    assert item.title == "Weekly reflection, Oct 19–Oct 25"
