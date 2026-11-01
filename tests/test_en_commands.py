"""An English Muninn says what the command runner, its tools, the topics, the conversation and the part done beside
a deep talk say in English."""

from __future__ import annotations

import json
import re

import pytest

from conftest import at, make_harness
from muninn.core.commands import parse
from muninn.core.sidecar import aside, first_sentences
from muninn.languages import language

EN = language("en")


def chinese(text: str) -> bool:
    return re.search(r"[一-鿿]", text) is not None


def say(h, plan: dict, text: str = "...") -> object:
    h.cloud.reply = h.local.reply = json.dumps(plan)
    return h.engine.command(text)


def english():
    return make_harness(at(2026, 10, 22, 10), lang="en")


def test_a_task_is_noted_in_english():
    r = say(english(), {"actions": [{"tool": "add_task", "args": {"title": "Buy paint", "due": "2026-11-01"}},
                                    {"tool": "add_task", "args": {"title": "Call the plumber", "due": None}}], "reply": ""})
    assert [o.text for o in r.outcomes] == ["Noted: T-1 Buy paint (by 2026-11-01)", "Noted: T-2 Call the plumber"]


def test_what_could_not_be_done_says_why_in_english():
    h = english()
    cases = [
        ({"tool": "set_status", "args": {"target": "acme", "status": "applied"}}, "Not done: I don't know acme"),
        ({"tool": "undo_action", "args": {}}, "Not done: Nothing done lately can be undone"),
        ({"tool": "rename_idea", "args": {"idea": "1"}}, "Not done: What should it be called?"),
        ({"tool": "alert_seen", "args": {}}, "Not done: Which reminder?"),
        ({"tool": "propose_domain", "args": {"id": "Bad Id", "title": "Pottery"}},
         "Not done: A new domain needs a name (and a lowercase id in English)"),
        ({"tool": "close_task", "args": {"task": "T-9"}}, "Not done: Which task?"),
        ({"tool": "dismiss_packet", "args": {}}, "Not done: Which application packet?"),
        ({"tool": "answer_question", "args": {"answer": "yes"}}, "Not done: No question is being answered right now"),
        ({"tool": "settle_topic", "args": {"topic": "the bike repair", "decision": "close"}}, "Not done: Can't find \"the bike repair\""),
        ({"tool": "settle_topic", "args": {"topic": "the bike repair", "decision": "maybe"}},
         "Not done: Close it, shelve it, or take it back up?"),
        ({"tool": "explore_idea", "args": {}}, "Not done: Which idea?"),
    ]
    for action, said in cases:
        [o] = say(h, {"actions": [action], "reply": "Done!"}).outcomes
        assert o.failed and o.text == said and not chinese(o.text)


def test_a_request_is_kept_in_english():
    r = say(english(), {"actions": [{"tool": "request_feature", "args": {"text": "Book train tickets for me"}}], "reply": ""})
    [o] = r.outcomes
    assert o.text == "Added to the request list (1 waiting); it will be dealt with next time"


def test_a_topic_is_settled_in_english():
    h = english()
    c = h.engine.conversation()
    for said, title in (("I want a new phone", "New phone"), ("When is the dentist?", "Dentist")):
        h.cloud.reply = h.local.reply = json.dumps({"actions": [], "reply": "OK", "topic": {"title": title, "state": "open", "now": ""}})
        c.execute_turn(c.understand_turn(None, said))
    out = []
    for decision in ("shelve", "reopen", "close"):
        [o] = say(h, {"actions": [{"tool": "settle_topic", "args": {"topic": "New phone", "decision": decision}}], "reply": ""}).outcomes
        out.append(o.text)
    assert out == ["Shelved: New phone", "Taken back up: New phone", "Closed: New phone"]


def test_what_is_not_understood_is_said_in_english():
    h = english()
    h.cloud.reply = h.local.reply = "not a plan"
    r = h.engine.command("Something odd")
    assert not r.understood and r.reply == "I didn't understand that, so I kept it as a note. Try saying it another way."
    r = say(h, {"actions": [], "reply": ""}, "It was a long day")
    assert r.reply == "Noted."


def test_with_no_model_set_up_it_says_so_in_english(tmp_path):
    from muninn.config import Config
    from muninn.main import build

    (tmp_path / "config.yaml").write_text("language: en\n")
    app = build(Config.load(tmp_path), dry_run=True)
    r = app.engine.commands().run("Hand in the report by next Wednesday")
    assert r.reply.startswith("No model is connected yet") and not chinese(r.reply) and not r.understood


def test_a_pressed_suggestion_that_is_gone_is_said_in_english():
    h = english()
    h.store.save_thread("t1", {"about": None, "ts": "2026-10-22T09:00", "turns": [], "suggest": []})
    with pytest.raises(ValueError, match="That suggestion is no longer there"):
        h.engine.conversation().act("t1", 0)


def test_english_replies_lose_the_sentences_that_send_the_user_to_a_slash_command():
    got = parse(json.dumps({"actions": [], "reply": "You can talk it through. Type /attach idea-1 to go on. The file is /Users/x/a.txt."}),
                words=EN)
    assert got["reply"] == "You can talk it through. The file is /Users/x/a.txt."


def test_beside_a_talk_muninn_says_a_sentence_or_two_and_tells_the_session_in_english():
    assert first_sentences("Your dentist is at 10:30. I'll remind you at 10. Anything else?", 2, EN) == \
        "Your dentist is at 10:30. I'll remind you at 10."
    told = aside("remind me to water the plants", EN)
    assert told == "(Muninn will take care of \"remind me to water the plants\" in this sentence; you don't need to.)"
