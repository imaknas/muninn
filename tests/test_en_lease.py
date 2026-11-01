"""Lending a conversation, and the permission requests a lent session makes, in English: what the user is told, what
the project's session is told, and what joins the conversation when the talk comes back."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from muninn.adapters.store_sqlite import SqliteStore
from muninn.core.lease import ERRORS, MODE_NOTES, PUSH_TITLES, ChannelLease, PermissionDesk, answer_text, card_from_text, friendly, handover_text
from muninn.core.ports import RunnerError
from muninn.languages import language

from conftest import FakeChannel, FakeClock, at
from test_lease import FakeRunner, Threads, ev

EN = language("en")


def english(text: str) -> bool:
    return re.search(r"[一-鿿]", text) is None


@pytest.fixture
def setup():
    store, clock, channel, runner, threads = SqliteStore(":memory:"), FakeClock(at(2026, 10, 22, 15)), FakeChannel(), FakeRunner(), Threads()
    leases = ChannelLease(store, clock, runner, channel, lambda now: False, threads.load, threads.update, words=EN)
    desk = PermissionDesk(store, clock, channel, lambda now: False, leases, words=EN)
    return store, clock, channel, runner, threads, leases, desk


def test_every_word_the_lease_looks_up_through_a_table_is_there():
    for key in [*ERRORS.values(), *MODE_NOTES.values(), *PUSH_TITLES.values()]:
        for code in ("zh-TW", "en"):
            assert language(code)(key, project="lilac")


def test_lending_a_conversation_is_said_in_english(setup):
    store, clock, _, runner, threads, leases, _ = setup
    with pytest.raises(ValueError, match=r"can't be lent to \"vault\"; it can go to: garden, blog") as e:
        leases.attach(None, "vault")
    assert english(str(e.value))
    got = leases.lend("t1", "garden")
    assert got["text"].startswith("What you say next goes straight to garden (it only reads") and english(got["text"])
    with pytest.raises(ValueError, match="already lent out"):
        leases.attach("t1", "blog")
    store.set_state("lease:rate_limit", {"five_hour": {"utilization": 0.86, "label": "5 hours", "resetsAt": clock.now().timestamp() + 3600}})
    assert leases.quota_warning() == "Usage so far: 5 hours 86%"
    assert leases.attach(None, "blog")["needs_confirm"] == "Usage so far: 5 hours 86%. Start anyway?"


def test_what_the_session_is_told_and_the_talk_keeps_is_in_english(setup):
    store, clock, _, runner, threads, leases, _ = setup
    runner.sandboxes = ["idea-4"]
    threads.d["t1"] = {"about": {"kind": "idea", "id": 4}, "turns": [
        {"role": "user", "text": "Could a rain barrel work?", "ts": "", "cloud_ok": True},
        {"role": "muninn", "text": "Roughly, yes.", "ts": ""}]}
    got = leases.attach("t1", "idea-4")
    [sent] = [c[1] for c in runner.calls if c[0] == "send"]
    assert "User: Could a rain barrel work?\nMuninn: Roughly, yes." in sent and sent.startswith("The user just talked this far")
    assert english(sent)
    assert store.lease_turns(got["lease_id"])[-1]["text"] == "Handed what we just talked about to idea-4; it carries on from there"
    leases.on_event(ev(got["lease_id"], 1, "turn_end"))
    leases.on_event(ev(got["lease_id"], 2, "attachments", paths=["a.png", "b.png"]))
    assert store.lease_turns(got["lease_id"])[-1]["text"] == "Saved to idea-4 (2 files)"
    note = leases.set_mode("t1", "discuss")["note"]
    assert note == "Back to read-only"
    with pytest.raises(ValueError, match="Hands-on or read-only"):
        leases.set_mode("t1", "both")
    leases.terminal("t1")
    with pytest.raises(ValueError, match="open in a terminal"):
        leases.send("t1", "hello")


def test_what_muninn_said_beside_a_talk_and_its_handover_come_back_in_english(setup):
    store, clock, _, runner, threads, leases, _ = setup
    leases._share_uncleared = True
    got = leases.attach(None, "garden")
    tid, lid = got["thread"], got["lease_id"]
    threads.d[tid]["topic"] = {"title": "Drainage", "state": "lent"}
    leases.send(tid, "Which plants first?")
    leases.on_event(ev(lid, 1, "turn_end"))
    assert leases.relay(lid, "Start with the sage")
    told = runner.calls[-1][1]
    assert told == "(Muninn just answered the user on the side; the part that concerns you: Start with the sage. Just so you know; no need to reply.)"
    leases.on_event(ev(lid, 2, "handover", handover={"summary": "Talked about drainage", "decisions": [{"what": "Dig a trench"}],
                                                      "open_questions": ["How deep?", "Gravel or sand?"],
                                                      "tasks_for_owner": [{"text": "Buy a shovel"}]}))
    leases.on_event(ev(lid, 3, "state", state="detached"))
    kept, back = threads.d[tid]["turns"]
    assert kept["text"] == "While you talked with garden, I said on the side:\n- Told garden: Start with the sage"
    assert back["text"] == ("The deep talk with garden is over.\nTalked about drainage\nDecided: Dig a trench\n"
                            "Still open: How deep?; Gravel or sand?\nTo-dos for you: Buy a shovel")
    assert back["handover"]["open_questions"] == ["How deep?", "Gravel or sand?"]
    assert threads.d[tid]["topic"]["now"] == "Talked with garden until Oct 22, 15:00"
    runner.calls.clear()
    leases.attach(tid, "garden")  # back again: it is asked what it left undecided
    sent = [c[1] for c in runner.calls if c[0] == "send"][-1]
    assert "Your last handover left these undecided" in sent and "- How deep?\n- Gravel or sand?" in sent and english(sent)


def test_an_english_handover_is_never_read_back_as_a_legacy_one(tmp_path):
    """A stored English handover keeps the card it was written with: reading cards back from text only knows the
    Chinese turns kept before cards were."""
    text = handover_text("garden", {"summary": "Talked about drainage"}, words=EN)
    assert card_from_text("garden", text) is None
    path = tmp_path / "m.db"
    s = SqliteStore(path)
    s.add_lease({"lease_id": "L1", "thread": "t1", "project": "garden", "mode": "discuss", "state": "detached",
                 "started": "2026-10-22T09:00", "ended": "2026-10-22T12:00"})
    card = {"project": "garden", "fallback": False, "summary": "Talked about drainage",
            "decisions": [{"what": "Dig", "why": "it floods"}], "open_questions": [], "tasks_for_owner": []}
    s.save_thread("t1", {"about": None, "ts": "x", "turns": [
        {"role": "muninn", "text": text, "ts": "a", "via": "lease:L1", "local_only": True, "handover": card}]})
    assert SqliteStore(path).thread("t1")["turns"][0]["handover"] == card


def test_the_runner_refusing_is_said_in_english(setup):
    *_, runner, _, leases, _ = setup
    assert friendly(RunnerError("cap_reached"), words=EN).startswith("As many conversations as allowed")
    assert friendly(RunnerError("odd"), words=EN) == "Something went wrong there (odd)."
    runner.fail = RunnerError("busy", "a terminal is open")
    with pytest.raises(ValueError, match="still busy there.* a terminal is open"):
        leases.attach(None, "garden")


def test_permission_requests_push_and_are_answered_in_english(setup):
    store, clock, channel, runner, _, leases, desk = setup
    runner.sandboxes = ["idea-4"]
    lid = leases.attach(None, "idea-4")["lease_id"]
    desk.receive({"id": "f", "lease": lid, "project": "idea-4", "kind": "write_file", "input_summary": "notes/plan.md"})
    desk.receive({"id": "c", "lease": lid, "project": "idea-4", "kind": "run_command", "input_summary": "python try.py"})
    desk.receive({"id": "w", "lease": lid, "project": "idea-4", "kind": "open_web", "input_summary": "https://seeds.example/a"})
    desk.receive({"id": "q", "lease": lid, "project": "idea-4", "kind": "ask_user", "question": "Which?"})
    assert [m[0].title for m in channel.sent] == ["idea-4 wants to change a file", "idea-4 wants to run a command",
                                                  "idea-4 wants to open a web page", "idea-4 asks you something"]
    with pytest.raises(ValueError, match="Every command is asked about"):
        desk.decide("c", "allow_session")
    with pytest.raises(ValueError, match="Pick an option"):
        desk.decide("q", "allow")
    assert desk.decide("f", "allow_session")["note"] == "Allowed changing files in this project's folder for this whole deep talk"
    assert desk.decide("c", "deny")["note"] == "Refused running: python try.py"
    assert desk.decide("w", "allow")["note"] == "Allowed opening seeds.example once"
    assert desk.decide("q", "allow", {"Which?": ["sage", "mint"]})["note"] == "You chose: sage, mint"
    with pytest.raises(ValueError, match="decided already"):
        desk.decide("c", "allow")
    assert answer_text({"kind": "ask_user", "decision": "allow", "answers": {"A?": "yes", "B?": "no"}}, words=EN) == "You chose: A? → yes; B? → no"
    clock.t += timedelta(minutes=1)
    desk.receive({"id": "o", "lease": lid, "project": "idea-4", "kind": "other", "input_summary": "tidy up"})
    clock.t += timedelta(minutes=6)
    desk.expire()
    assert store.lease_turns(lid)[-1]["text"] == "No answer, so it didn't do: tidy up"
