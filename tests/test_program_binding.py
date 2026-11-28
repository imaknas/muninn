"""Requesters that are programs (the Principal Handoff Program binding, proposed for 0.7): a program acts on an option
chosen, or a to-do done or dropped, in the user's own word or his yes, and on nothing else. So Muninn offers a
program's options as the way to answer and records the label word for word with no note, never settles a program's
to-do on its own reading or the standing permission, and says so on the card."""

from __future__ import annotations

from datetime import timedelta

import pytest

from conftest import at, make_harness

P = "garden"
PROGRAM = "project:backup"


def rec(type_, key, ext, by=PROGRAM, id_=None, origin="requester", **more):
    return {"ph": "0.6", "ext": ext, "id": id_ or f"{type_[0]}:{key}", "type": type_, "origin": origin, "by": by,
            "key": key, "project": P, "ts": "2026-10-22T09:00:00Z", **more}


def decision(key, by=PROGRAM):
    return rec("raised", key, "decisions/0.6", by=by, id_=f"r:{key}", terms_rev=1, terms={
        "text": "刪掉三月的 10 個快照,還是留著?只有你選的會被執行。", "options": [
            {"id": "delete", "label": "刪掉"}, {"id": "keep", "label": "留著"}], "default": "keep"})


def act(key, by=PROGRAM):
    return rec("raised", key, "acts/0.6", by=by, id_=f"r:{key}", terms_rev=1, terms={"text": "換掉備份碟"})


@pytest.fixture
def h():
    return make_harness(at(2026, 10, 22, 10), programs=[PROGRAM])


def told(h):
    h.clock.t += timedelta(hours=13)  # past any undo window
    return [r for r in h.engine.transport.fetch(h.engine.transport_credential, P, None, PROGRAM, None)["records"]
            if r["origin"] == "principal"]


@pytest.fixture(autouse=True)
def relay(h):
    from muninn.core.transport import Credential

    h.engine.transport_credential = Credential("platform")


def test_a_programs_question_offers_its_options_and_one_chosen_is_its_label_word_for_word(h):
    h.engine.records_in.receive([decision("backup-snapshots")])
    [q] = h.engine.messages.open_questions()
    assert q["program"] and [o["label"] for o in q["options"]] == ["刪掉", "留著"]
    h.engine.messages.answer("backup-snapshots", option="delete")
    [s] = told(h)
    assert s["answer"] == {"text": "刪掉", "option": "delete"} and "note" not in s
    assert s["authority"] == {"kind": "principal", "decider": h.engine.parties.principal}


def test_an_answer_in_his_words_is_text_alone_and_an_unknown_option_is_refused(h):
    h.engine.records_in.receive([decision("backup-snapshots"), decision("backup-logs", by="project:garden")])
    with pytest.raises(ValueError):
        h.engine.messages.answer("backup-snapshots", option="burn")
    h.engine.messages.answer("backup-snapshots", "刪掉,但留三月十號那個")
    [s] = told(h)
    assert s["answer"] == {"text": "刪掉,但留三月十號那個"}  # the program asks again
    assert not next(q for q in h.engine.messages.open_questions() if q["id"] == "backup-logs")["program"]


def test_a_programs_to_do_is_settled_only_on_his_word_or_his_yes(h):
    h.engine.tasks.grant_claims_mandate("專案說做完了就記成做完,我可以撤回")
    h.engine.records_in.receive([act("backup-disk"), act("garden-pots", by="project:garden")])
    claim = {"origin": "agent", "by": "project:garden", "outcome": "done", "evidence": {"basis": "看到新碟了"}}
    h.engine.records_in.receive([rec("claimed", "backup-disk", "acts/0.6", id_="c:backup-disk:1", **claim),
                                 rec("claimed", "garden-pots", "acts/0.6", id_="c:garden-pots:1", **claim)])
    assert h.engine.tasks.get("backup-disk")["status"] == "open"  # the standing permission is not for a program's
    assert h.engine.tasks.get("garden-pots")["status"] == "done"
    h.engine.tasks.close("backup-disk", by="evidence", why="他說換好了")  # nor is Muninn's reading of his words
    assert h.engine.tasks.get("backup-disk")["status"] == "open"
    c = next(c for c in h.engine.pending_confirmations() if c.kind == "action")
    h.engine.answer(c.id, c.options[0])
    [s] = [r for r in told(h) if r["key"] == "backup-disk"]
    assert s["authority"]["kind"] == "confirmed" and "note" not in s


def test_his_own_close_of_a_programs_to_do_carries_no_note_and_the_home_says_what_it_acts_on(h):
    h.engine.records_in.receive([act("backup-disk"), act("garden-pots", by="project:garden")])
    h.engine.tasks.close("backup-disk", why="換好了")
    h.engine.tasks.close("garden-pots", why="買好了")
    settled = {r["key"]: r for r in told(h)}
    assert "note" not in settled["backup-disk"] and settled["garden-pots"]["note"] == "買好了"
    h.engine.records_in.receive([act("backup-cable")])
    [t] = [t for t in h.engine.home_view()["tasks"] if t["key"] == "backup-cable"]
    assert t["program"] is True


def test_nothing_is_a_program_unless_configured(h):
    plain = make_harness(at(2026, 10, 22, 10))
    plain.engine.records_in.receive([decision("backup-snapshots")])
    [q] = plain.engine.messages.open_questions()
    assert q["program"] is False and len(q["options"]) == 2
