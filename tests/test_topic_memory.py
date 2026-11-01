"""What a topic remembers in a conversation that never ends: every line kept, a state refreshed from the lines
that left the window, the user's own words read as they are, and earlier lines brought back for a sentence."""

from __future__ import annotations

import json

import pytest

from muninn.adapters.store_sqlite import SqliteStore
from muninn.core.llm_router import Completion
from muninn.core.topic_memory import FOLD_AT, HIS_WORDS, WINDOW, TopicMemory, View

from conftest import ZH, FakeClock, at, make_harness


class FakeRouter:
    """Answers each point with what the test set; records what each was asked."""

    def __init__(self):
        self.replies: dict[str, str | None] = {}
        self.asked: list[tuple[str, dict]] = []

    def complete_case(self, req):
        self.asked.append((req.task, json.loads(req.content)))
        reply = self.replies.get(req.task)
        return Completion(reply, "fake" if reply else None, None)


def chat(n: int, planted: dict[int, str] | None = None) -> list[dict]:
    """n lines, the user's and Muninn's in turn; `planted`: lines with a detail worth finding again."""
    planted = planted or {}
    return [{"role": "user" if i % 2 == 0 else "muninn", "text": planted.get(i, f"第 {i} 句,在聊陽台的花架"),
             "ts": "", "cloud_ok": True} for i in range(n)]


STATE = json.dumps({"decided": ["花架用松木,寬 120 公分"], "open": ["等他量欄杆高度"], "next": "量欄杆",
                    "where": [{"what": "花架尺寸", "lines": [2]}]}, ensure_ascii=False)


@pytest.fixture
def setup():
    store, clock, router = SqliteStore(":memory:"), FakeClock(at(2026, 10, 22, 15)), FakeRouter()
    opened = []
    memory = TopicMemory(store, clock, router, lambda: opened, words=ZH)
    return store, router, memory, opened


def test_a_short_conversation_is_read_whole_and_nothing_is_asked(setup):
    store, router, memory, _ = setup
    turns = chat(WINDOW)
    store.save_thread("t1", {"turns": turns, "ts": "2026-10-22T15:00:00"})
    assert memory.refresh() == 0
    v = memory.view("t1", turns, "那就這樣")
    assert v.recent == turns and v.earlier == [] and v.state is None and router.asked == []


def test_lines_that_left_the_window_are_read_as_they_are_until_the_state_has_them(setup):
    store, router, memory, _ = setup
    turns = chat(WINDOW + FOLD_AT - 1)
    store.save_thread("t1", {"turns": turns, "ts": "2026-10-22T15:00:00"})
    assert memory.refresh() == 0 and router.asked == []  # not enough out of the window yet
    v = memory.view("t1", turns, "那就這樣")
    assert v.recent == turns and router.asked == []  # nothing is dropped while the state lags


def test_the_state_folds_in_what_left_the_window_and_points_back_to_the_lines(setup):
    store, router, memory, _ = setup
    turns = chat(WINDOW + FOLD_AT, {2: "花架做寬 120 公分,用松木"})
    store.save_thread("t1", {"turns": turns, "ts": "2026-10-22T15:00:00"})
    router.replies["topic_state"] = STATE
    assert memory.refresh() == 1
    point, asked = router.asked[0]
    assert point == "topic_state" and asked["previous"] is None and [x["i"] for x in asked["lines"]] == list(range(FOLD_AT))
    kept = memory.memory("t1")
    assert kept["upto"] == FOLD_AT and kept["state"]["where"] == [{"what": "花架尺寸", "lines": [2]}]
    assert memory.refresh() == 0  # nothing new out of the window: not asked again
    turns += chat(FOLD_AT)
    store.save_thread("t1", {"turns": turns, "ts": "2026-10-22T16:00:00"})
    memory.refresh()
    _, again = router.asked[-1]
    assert again["previous"]["decided"] == ["花架用松木,寬 120 公分"]  # from the last state and the new lines only
    assert [x["i"] for x in again["lines"]] == list(range(FOLD_AT, 2 * FOLD_AT))


def test_a_state_reply_that_makes_no_sense_keeps_the_last_one(setup):
    store, router, memory, _ = setup
    turns = chat(WINDOW + FOLD_AT)
    store.save_thread("t1", {"turns": turns, "ts": "2026-10-22T15:00:00"})
    router.replies["topic_state"] = "我覺得還不錯"
    assert memory.refresh() == 0 and memory.memory("t1") is None
    assert memory.view("t1", turns, "那就這樣").recent == turns  # still read as they are


def test_a_long_conversation_reads_the_state_his_own_words_and_the_lines_brought_back(setup):
    store, router, memory, opened = setup
    turns = chat(60, {3: "Muninn:那寬度就照你說的 120 公分", 7: "Muninn:松木一片 380 元"})
    store.save_thread("t1", {"turns": turns, "ts": "2026-10-22T15:00:00"})
    router.replies["topic_state"] = STATE
    memory.refresh()  # folds 0..47 in, the window is the last 12
    assert memory.memory("t1")["upto"] == 60 - WINDOW
    router.replies["topic_recall"] = json.dumps({"lines": [7, 3, 999, 7, "x"], "why": "價錢和尺寸"})
    opened.extend([{"thread": "t1", "title": "花架", "state": "waiting"}, {"thread": "t2", "title": "牙醫", "state": "waiting"}])
    v = memory.view("t1", turns, "那一片多少錢?")
    assert v.recent == turns[-WINDOW:]
    his = [i for i in range(60 - WINDOW) if i % 2 == 0][-HIS_WORDS:]
    assert [x["i"] for x in v.earlier] == sorted([3, 7, *his])  # only lines that exist, once each
    assert "380 元" in next(x["text"] for x in v.earlier if x["i"] == 7)
    assert v.state["decided"] == ["花架用松木,寬 120 公分"] and v.state["cloud_ok"] is True
    point, asked = router.asked[-1]
    assert point == "topic_recall" and asked["said"] == "那一片多少錢?" and asked["state"]["next"] == "量欄杆"
    assert not {x["i"] for x in asked["earlier"]} & set(his)  # his own words are read anyway: not offered again
    assert v.others == [{"title": "牙醫", "state": "waiting", "cloud_ok": True}]  # the other open topics, not this one


def test_nothing_brought_back_when_the_picker_does_not_answer(setup):
    store, router, memory, _ = setup
    turns = chat(60)
    store.save_thread("t1", {"turns": turns, "ts": "2026-10-22T15:00:00"})
    router.replies["topic_state"] = STATE
    memory.refresh()
    v = memory.view("t1", turns, "那一片多少錢?")
    assert all(x["role"] == "user" for x in v.earlier) and len(v.earlier) == HIS_WORDS


def test_a_state_made_from_lines_that_were_not_cleared_or_came_from_a_lent_session_says_so(setup):
    store, router, memory, _ = setup
    turns = chat(WINDOW + FOLD_AT)
    turns[1] = {**turns[1], "local_only": True, "via": "lease:abc"}
    turns[4] = {**turns[4], "cloud_ok": False}
    store.save_thread("t1", {"turns": turns, "ts": "2026-10-22T15:00:00"})
    router.replies["topic_state"] = STATE
    memory.refresh()
    flags = memory.memory("t1")["flags"]
    assert flags == {"cloud_ok": False, "local_only": True, "via": ["lease:abc"]}


def test_the_round_reads_what_the_view_gives_and_carries_its_flags():
    h = make_harness(at(2026, 10, 22, 10))
    turns = chat(WINDOW)
    view = View(turns, [{**chat(1, {0: "花架做寬 120 公分"})[0], "i": 2}],
                {"decided": ["花架用松木"], "open": [], "next": "", "where": [], "cloud_ok": True, "local_only": True,
                 "via": ["lease:abc"]},
                [{"title": "牙醫", "state": "waiting", "cloud_ok": True}])
    h.local.reply = h.cloud.reply = json.dumps({"actions": [], "reply": "好"})
    plan = h.engine.commands().understand("那一片多少錢?", history=turns, view=view)
    sent = json.loads(next(p for b in (h.local, h.cloud) for _, p in b.calls if '"said"' in p))
    assert sent["topic_state"] == {"decided": ["花架用松木"], "open": [], "next": "", "where": []}
    assert sent["earlier"] == [{"i": 2, "who": "the user", "text": "花架做寬 120 公分"}]
    assert sent["other_topics"] == [{"title": "牙醫", "state": "waiting"}] and len(sent["conversation"]) == WINDOW
    assert plan["local_only"] and plan["via"] == ["lease:abc"]  # only that session's project may hear of this round


def test_a_conversation_that_went_on_in_a_deep_talk_is_folded_again_to_its_last_line(setup):
    store, router, memory, _ = setup
    turns = chat(WINDOW + FOLD_AT)
    store.save_thread("t1", {"turns": turns, "ts": "2026-10-22T15:00:00"})
    router.replies["topic_state"] = STATE
    memory.refresh()
    turns += [{"role": "muninn", "text": "跟 garden 談完了:欄杆量好了 95 公分", "ts": "", "local_only": True, "via": "lease:L1"}]
    store.save_thread("t1", {"turns": turns, "ts": "2026-10-22T16:00:00"})
    assert memory.refresh() == 0  # one line more is not enough on its own
    memory.went_on("t1")
    assert memory.refresh() == 1
    _, asked = router.asked[-1]
    assert asked["lines"][-1]["text"].startswith("跟 garden 談完了")  # up to the talk's end, not just what left the window
    kept = memory.memory("t1")
    assert "refold" not in kept and kept["flags"]["via"] == ["lease:L1"] and kept["flags"]["local_only"]
    assert memory.refresh() == 0
