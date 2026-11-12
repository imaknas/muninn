"""A question about mail is answered from every mail Muninn has read: found by any of its terms (not the whole sentence
as one string), listed, sorted or added up by the model, the mail it rests on most opened; and the mail is read only
where its own rule lets it be."""

from __future__ import annotations

import json
from datetime import timedelta

from conftest import at, make_harness
from muninn.core.mail import MailTriager
from muninn.core.mail_answer import LATEST, pick
from muninn.core.models import MailMessage, Observation
from muninn.core.privacy import Cleared
from test_actions import FakeMailbox

BANK = '"北辰銀行" <card@beichen.example>'


def bill(i, month, amount, sender=BANK, account="personal", days_ago=0):
    ts = at(2026, 10, 22, 9) - timedelta(days=days_ago)
    m = MailMessage(f"b{i}", f"t{i}", account, account, sender, "me@example.com", "信用卡帳單繳款通知", ts,
                    f"親愛的卡友,您 {month} 月帳單應繳 {amount} 元,繳款截止日為下月 15 日。", f"<b{i}@beichen>")
    note = Observation("mail", ts, "mail", f"mail:{account}:{m.id}",
                       {"account": account, "id": m.id, "thread": m.thread_id, "from": sender, "subject": m.subject,
                        "category": "deadline", "summary": f"北辰銀行信用卡 {month} 月帳單應繳 {amount} 元", "action": "確認扣款帳戶餘額"})
    return m, note


def other(i, days_ago):
    ts = at(2026, 10, 22, 9) - timedelta(days=days_ago)
    return Observation("mail", ts, "mail", f"mail:personal:o{i}",
                       {"account": "personal", "id": f"o{i}", "thread": f"o{i}", "from": "Garden Club <news@garden.example>",
                        "subject": f"Newsletter {i}", "category": "promo", "summary": "This month's planting tips", "action": None})


def harness(mails, notes=(), cloud_mail=True):
    h = make_harness(at(2026, 10, 22, 10))
    h.engine.mailboxes = [FakeMailbox("personal", "personal", [m for m in mails if m.account == "personal"]),
                          FakeMailbox("work", "work", [m for m in mails if m.account == "work"])]
    h.engine.triager = MailTriager(h.engine.router, cloud_mail=cloud_mail)
    h.store.add_observations(notes)
    return h


def answering(h, *texts):
    """The local model's replies in turn: the routing, then the mail question."""
    queue = [json.dumps(t, ensure_ascii=False) if isinstance(t, dict) else t for t in texts]

    def complete(system, prompt, *, json_mode=False):
        h.local.calls.append((system, prompt))
        return queue.pop(0)

    h.local.complete = complete


def route(question, *terms):
    return {"actions": [{"tool": "find_mail", "args": {"question": question, "terms": list(terms)}}], "reply": "我查一下"}


def test_a_mail_is_found_by_any_of_its_terms_not_by_the_whole_question_as_one_string():
    _, sept = bill(1, 9, "12,300", days_ago=30)
    _, octo = bill(2, 10, "40,377")
    notes = [sept, octo, *(other(i, i) for i in range(5))]
    picked, matched = pick(notes, ["北辰", "信用卡 帳單", "帳單"])  # the second term as a whole is in no mail
    assert matched and picked == [octo, sept]  # most terms matched, then newest
    picked, matched = pick(notes, ["Northstar Bank"])  # written another way: the latest, for the model to recognise
    assert not matched and picked[0] == octo and len(picked) == len(notes)
    many = [other(i, i) for i in range(LATEST + 10)]
    assert len(pick(many, ["nothing"])[0]) == LATEST


def test_bills_are_listed_from_every_mail_read_and_the_one_it_rests_on_most_opens():
    sept_m, sept = bill(1, 9, "12,300", days_ago=30)  # older than three weeks: still read
    octo_m, octo = bill(2, 10, "40,377")
    h = harness([sept_m, octo_m], [sept, octo, other(1, 3)])
    answering(h, route("列出北辰銀行信用卡帳單,金額從大排到小", "北辰", "北辰銀行", "信用卡", "帳單"),
              {"answer": "1. 10 月 40,377 元\n2. 9 月 12,300 元", "mails": ["m1", "m2"]})
    r = h.engine.command("列出北辰信用卡帳單 從大排到小")
    assert r.reply == ""  # a lookup answers with its result
    (o,) = r.outcomes
    assert not o.failed and o.text == "1. 10 月 40,377 元\n2. 9 月 12,300 元"
    assert o.link == "#mail/personal:b2"  # m1 is the newest match
    system, prompt = h.local.calls[-1]
    data = json.loads(prompt)
    assert data["matched"] is True and data["question"] == "列出北辰銀行信用卡帳單,金額從大排到小"
    assert [m["subject"] for m in data["mails"]] == ["信用卡帳單繳款通知"] * 2  # the newsletter is not among them
    assert "40,377 元,繳款截止日" in data["mails"][0]["body"]  # the best matches' text is read again
    assert data["mail_since"] == "2026-09-22"


def test_nothing_about_it_is_said_as_an_answer_not_as_something_that_failed():
    _, octo = bill(2, 10, "40,377")
    h = harness([], [octo])
    answering(h, route("房東寄來的租約", "房東", "租約"), {"answer": "9 月 22 日以來讀過的信裡,沒有跟租約有關的。", "mails": []})
    (o,) = h.engine.command("房東寄來的租約").outcomes
    assert not o.failed and o.text.startswith("9 月 22 日以來") and o.link is None
    assert json.loads(h.local.calls[-1][1])["matched"] is False  # the latest were read, in case it is written another way


def test_no_mail_read_yet_is_said_without_asking_a_model_and_no_answer_is_said_as_such():
    h = harness([])
    answering(h, route("信用卡帳單", "帳單"))
    (o,) = h.engine.command("信用卡帳單").outcomes
    assert o.text == "我還沒讀過任何信" and len(h.local.calls) == 1
    _, octo = bill(2, 10, "40,377")
    h = harness([], [octo])
    answering(h, route("信用卡帳單", "帳單"), "not json")
    (o,) = h.engine.command("信用卡帳單").outcomes
    assert o.failed and "沒有模型回答" in o.text


def test_work_mail_and_a_question_not_cleared_stay_off_the_cloud():
    from muninn.core.mail_answer import MailQuestions

    personal_m, personal = bill(1, 10, "40,377")
    work_m, work = bill(2, 10, "8,000", account="work")
    for mails, notes, cleared, cloud in (([personal_m], [personal], Cleared("帳單"), True),
                                         ([personal_m, work_m], [personal, work], Cleared("帳單"), False),
                                         ([personal_m], [personal], None, False)):
        h = harness(mails, notes)
        h.cloud.reply = h.local.reply = json.dumps({"answer": "10 月 40,377 元", "mails": ["m1"]})
        q = MailQuestions(h.store, h.clock, lambda: h.engine.mailboxes, lambda: h.engine.triager, words=h.engine.words)
        assert q.answer("信用卡帳單", ["帳單"], cleared).text == "10 月 40,377 元"
        assert bool(h.cloud.calls) == cloud, (len(mails), cleared)
