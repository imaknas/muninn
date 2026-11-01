"""An idea moves forward without being told: after a round about it, Muninn tries it out, sets it aside, or asks
once whether to make it a project; an experiment that comes back is reported where the idea is talked about."""

from __future__ import annotations

import json

import pytest

from muninn.adapters.store_sqlite import SqliteStore
from muninn.core.decide import Verdict
from muninn.core.idea_next import IdeaNext, IdeaReports, question
from muninn.core.ports import SandboxRefused

from conftest import ZH, FakeClock, at, make_harness


class Decider:
    def __init__(self, label: str | None = "discuss"):
        self.label = label
        self.asked = []

    def decide(self, q):
        self.asked.append(q)
        return Verdict(self.label, None, "fake") if self.label else None


IDEA = {"kind": "idea", "ref": 4, "title": "用照片量房間尺寸", "status": "open"}


def plan(next_step=None, actions=(), reply="可以試試看。", about=IDEA):
    return {"actions": list(actions), "reply": reply, "suggest": [], "about": about, "next": next_step}


def apply(p, said="好", acted=False, marks=()):
    IdeaNext(Decider(), lambda n: IDEA, words=ZH).apply(p, said, acted=acted, marks=marks)
    return p


def test_trying_it_out_opens_the_experiment_and_offering_to_is_only_a_button():
    assert apply(plan("experiment"))["actions"] == [{"tool": "explore_idea", "args": {"idea": 4}}]
    offered = apply(plan("offer_experiment"))
    assert offered["actions"] == [] and offered["suggest"] == [{"label": "開沙盒試", "tool": "explore_idea", "args": {"idea": 4}}]
    assert "next" not in offered  # used up: never reaches what is carried out


def test_put_off_it_is_set_aside_and_discuss_does_nothing():
    p = apply(plan("shelve"), said="先這樣,之後再說")
    assert p["actions"] == [{"tool": "settle_idea", "args": {"idea": 4, "decision": "shelve", "why": "先這樣,之後再說"}}]
    for step in ("discuss", None):
        q = apply(plan(step))
        assert q["actions"] == [] and q["suggest"] == [] and q["reply"] == "可以試試看。"


def test_nothing_more_is_done_when_the_round_settled_the_idea_itself_or_is_not_about_one():
    assert apply(plan("experiment"), acted=True)["actions"] == []
    assert apply(plan("experiment", about={"kind": "task", "ref": "t1"}))["actions"] == []


def test_making_it_a_project_is_asked_once_with_a_button_and_marks_the_conversation():
    p = plan("promote", reply="差 5 公分,很準。")
    p["suggest"] = [{"label": "再量一間", "tool": "explore_idea", "args": {}}]
    apply(p)
    assert p["reply"] == "差 5 公分,很準。\n" + question("idea-4", ZH) and p["mark"] == ["asked_promote"]
    assert p["suggest"][0] == {"label": "升級成專案", "tool": "promote_idea", "args": {"idea": 4}} and len(p["suggest"]) == 2
    again = apply(plan("promote", reply="好"), marks=["asked_promote"])
    assert again["reply"] == "好" and again["suggest"] == [] and "mark" not in again


def test_a_reply_never_sends_the_user_to_a_slash_command():
    from muninn.core.commands import parse

    got = parse(json.dumps({"actions": [], "reply": "可以跟它深談。打 /attach idea-1 就能接過去。路徑在 /Users/x/a.txt。"},
                           ensure_ascii=False), words=ZH)
    assert got["reply"] == "可以跟它深談。路徑在 /Users/x/a.txt。"
    assert parse(json.dumps({"actions": [], "reply": "打 /detach 收回", "next": "nope"}), words=ZH)["reply"] == ""
    assert parse(json.dumps({"actions": [], "reply": "好", "next": "nope"}), words=ZH)["next"] is None


@pytest.fixture
def reports():
    store, clock = SqliteStore(":memory:"), FakeClock(at(2026, 10, 22, 15))
    said, pushed, quiet = [], [], {"on": False}
    threads = {"t1": {"turns": [{"role": "user", "text": "用這三張照片重建客廳"}]}}
    d = Decider("promote")
    r = IdeaReports(store, clock, IdeaNext(d, lambda n: IDEA, words=ZH), lambda name: 4 if name == "idea-4" else None, threads.get,
                    lambda tid, text, buttons, mark: said.append((tid, text, buttons, mark)), lambda now: quiet["on"], pushed.append, words=ZH)
    return r, d, said, pushed, quiet, threads


def test_an_experiment_that_came_back_is_reported_in_its_ideas_conversation(reports):
    r, d, said, pushed, _, _ = reports
    r.returned({"project": "garden", "thread": "t9"}, "跟 garden 的深談結束了。")  # not an idea's sandbox: nothing
    r.returned({"project": "idea-4", "thread": "t1"}, "跟 idea-4 的深談結束了。長牆 4.1 m,短牆 3.3 m。")
    assert r.run(lambda fn: fn()) == 1
    assert json.loads(d.asked[0].content)["returned"].endswith("短牆 3.3 m。")
    assert said == [("t1", question("idea-4", ZH), [{"label": "升級成專案", "tool": "promote_idea", "args": {"idea": 4}}], "asked_promote")]
    assert pushed == ["idea-4 的實驗做完了"]
    assert r.run(lambda fn: fn()) == 0  # reported once


def test_a_report_says_nothing_more_when_there_is_nothing_to_ask_and_no_push_in_quiet_hours(reports):
    r, d, said, pushed, quiet, threads = reports
    d.label = "discuss"
    quiet["on"] = True
    r.returned({"project": "idea-4", "thread": "t1"}, "跟 idea-4 的深談結束了。")
    assert r.run(lambda fn: fn()) == 1 and said == [] and pushed == []
    d.label = "promote"
    threads["t1"]["marks"] = ["asked_promote"]
    r.returned({"project": "idea-4", "thread": "t1"}, "又跑了一次")
    r.run(lambda fn: fn())
    assert said == []  # asked before


def test_promoting_an_idea_makes_its_sandbox_a_project_and_says_so_plainly_when_it_cannot():
    h = make_harness(at(2026, 10, 22, 10))
    ideas = h.engine.ideas
    opened, upgraded = [], []
    ideas._sandbox = lambda name, purpose: (opened.append(name), name)[1]
    n = ideas.capture("用照片量房間尺寸")[1]
    with pytest.raises(ValueError, match="還不能把想法升級"):
        ideas.promote(n)
    assert opened == [f"idea-{n}"]  # a project for it is opened either way: it is where it will be built
    ideas._upgrade = lambda name, rename: (upgraded.append((name, rename)), rename or name)[1]
    assert ideas.promote(n, "Shelf-Planner") == "shelf-planner"
    assert upgraded == [(f"idea-{n}", "shelf-planner")]
    assert ideas.get(n)["status"] == "promoted" and ideas.get(n)["project"] == "shelf-planner"
    assert ideas.explore(n) == "shelf-planner"  # talked to by its new name
    assert ideas.promote(n) == "shelf-planner" and len(upgraded) == 1  # already a project
    assert ideas.of_sandbox("shelf-planner") == n
    other = ideas.capture("另一個點子")[1]
    with pytest.raises(ValueError, match="只能用小寫英文"):
        ideas.promote(other, "Room Planner!")


def test_a_refused_upgrade_is_said_in_words():
    h = make_harness(at(2026, 10, 22, 10))
    ideas = h.engine.ideas
    ideas._sandbox = lambda name, purpose: name

    def taken(name, rename):
        raise SandboxRefused("taken")

    ideas._upgrade = taken
    n = ideas.capture("用照片量房間尺寸")[1]
    with pytest.raises(ValueError, match="升級成專案不行"):
        ideas.promote(n, "garden")


def test_a_discussion_round_that_can_be_tried_out_hands_the_conversation_to_the_sandbox():
    """Through the whole round: the routing reply said to try it out, with no tool of its own; it was opened."""
    h = make_harness(at(2026, 10, 22, 10))
    h.engine.ideas._sandbox = lambda name, purpose: name
    n = h.engine.ideas.capture("用照片量房間尺寸")[1]
    h.cloud.reply = h.local.reply = json.dumps({"actions": [], "reply": "那就拿客廳的照片試。", "next": "experiment"},
                                                ensure_ascii=False)
    conv = h.engine.conversation()
    turn = conv.understand_turn(None, "先看量得準不準", {"kind": "idea", "id": str(n)})
    r = conv.execute_turn(turn)
    assert [o.lend for o in r.outcomes] == [f"idea-{n}"]


def test_the_question_to_make_it_a_project_is_kept_on_the_conversation():
    h = make_harness(at(2026, 10, 22, 10))
    n = h.engine.ideas.capture("用照片量房間尺寸")[1]
    h.cloud.reply = h.local.reply = json.dumps({"actions": [], "reply": "很準。", "next": "promote"}, ensure_ascii=False)
    conv = h.engine.conversation()
    first = conv.execute_turn(conv.understand_turn(None, "4.05,很準", {"kind": "idea", "id": str(n)}))
    assert question(f"idea-{n}", ZH) in first.reply
    tid = h.store.threads_about("idea", str(n))[0][0]
    assert conv.thread(tid)["marks"] == ["asked_promote"]
    second = conv.execute_turn(conv.understand_turn(tid, "那接下來呢"))
    assert question(f"idea-{n}", ZH) not in second.reply


def test_an_idea_made_a_project_is_talked_of_as_that_project_and_never_offered_to_be_one_again():
    h = make_harness(at(2026, 10, 22, 10))
    ideas = h.engine.ideas
    ideas._sandbox = lambda name, purpose: name
    ideas._upgrade = lambda name, rename: rename or name
    n = ideas.capture("用照片重建房間的 3D 模型")[1]
    h.store.save_thread("t1", {"about": {"kind": "idea", "id": str(n)}, "ts": "2026-10-22T09:00:00+08:00",
                               "turns": [{"role": "user", "text": "房間 3D", "ts": "x"}],
                               "topic": {"title": "房間 3D", "state": "open", "now": "", "ts": "2026-10-22T09:00:00+08:00"}})
    ideas.promote(n, "shelf-planner")
    topic = ideas.topic("idea", str(n))
    assert topic["title"] == "shelf-planner" and topic["project"] == "shelf-planner" and "made into the project shelf-planner" in topic["note"]
    assert [t["title"] for t in h.engine.topics().today() if t["thread"] == "t1"] == ["shelf-planner"]  # its conversation's name too
    p = {"actions": [], "reply": "好", "suggest": [], "about": {**topic, "ref": n}, "next": "promote"}
    IdeaNext(Decider(), lambda k: ideas.topic("idea", str(k)), words=ZH).apply(p, "繼續", acted=False, marks=())
    assert p["suggest"] == [] and "升級" not in p["reply"]  # it goes on there; nothing to make of it here
