"""Taking back what Muninn did by saying so, and the conversations as one stream."""

from __future__ import annotations

import json

from conftest import at, make_harness


def say(h, text, *actions, about=None):
    h.cloud.reply = h.local.reply = json.dumps({"actions": [{"tool": t, "args": a} for t, a in actions], "reply": ""}, ensure_ascii=False)
    c = h.engine.conversation()
    return c.execute_turn(c.understand_turn(None, text, about)).outcomes


def test_what_is_named_is_taken_back_and_nothing_named_means_the_latest():
    h = make_harness(at(2026, 10, 25, 10))
    say(h, "記一下", ("capture_idea", {"idea": "做一個澆花提醒"}))
    say(h, "還有一個", ("capture_idea", {"idea": "每週讀一篇論文"}))
    [undone] = say(h, "想法不用記了", ("undo_action", {"what": "澆花提醒的想法"}))
    assert undone.text == "撤回了:記下這個想法了:做一個澆花提醒"
    assert [i["text"] for i in h.engine.ideas.all()] == ["每週讀一篇論文"]
    [latest] = say(h, "撤回", ("undo_action", {}))
    assert latest.text == "撤回了:記下這個想法了:每週讀一篇論文"
    [none] = say(h, "撤回", ("undo_action", {}))
    assert none.text == "沒做成:最近沒有可以撤回的事"


def test_two_things_that_both_fit_are_asked_about():
    h = make_harness(at(2026, 10, 25, 10))
    say(h, "記一下", ("capture_idea", {"idea": "花園的澆水排程"}))
    say(h, "還有", ("capture_idea", {"idea": "花園的施肥排程"}))
    [asked] = say(h, "花園那個撤回", ("undo_action", {"what": "花園的排程"}))
    assert asked.text.startswith("沒做成:是哪一件?") and len(h.engine.ideas.all()) == 2
    [missed] = say(h, "撤回牙醫", ("undo_action", {"what": "牙醫"}))
    assert missed.text.startswith("沒做成:找不到「牙醫」")


def test_the_conversations_are_one_stream_oldest_first_with_what_each_is_about():
    h = make_harness(at(2026, 10, 25, 10))
    say(h, "今天先做投遞")
    h.clock.t = at(2026, 10, 25, 11)
    n = h.engine.ideas.add("做一個澆花提醒")["n"]
    say(h, "這個值得做嗎", about={"kind": "idea", "id": str(n)})
    first, second = h.engine.conversation().feed()
    assert first["about"] is None and first["title"] is None and [t["role"] for t in first["turns"]] == ["user", "muninn"]
    assert second["about"] == {"kind": "idea", "id": str(n)} and second["title"] == "做一個澆花提醒"
    assert second["turns"][0]["text"] == "這個值得做嗎"
