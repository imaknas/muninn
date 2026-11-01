from __future__ import annotations

import json

import pytest

from conftest import ZH, at, make_harness
from test_understanding import harness as cloud_harness

IDEA = "拍室內照片轉成 3D 模型,再給空間規劃,想看看可不可行"


def route(h, tool, args):
    h.cloud.reply = h.local.reply = json.dumps({"actions": [{"tool": tool, "args": args}], "reply": ""})


def test_an_idea_unrelated_to_any_plan_is_kept_and_let_go_on_undo():
    h = make_harness(at(2026, 10, 22, 10))
    c = h.engine.conversation()
    route(h, "capture_idea", {"idea": IDEA})
    calls = len(h.cloud.calls) + len(h.local.calls)
    [outcome] = c.execute_turn(c.understand_turn(None, "我在想能不能拍室內照片讓 Claude 轉成 3D 模型,再給我空間規劃")).outcomes
    assert outcome.text == f"記下這個想法了:{IDEA}" and outcome.action_id  # a number is no name to say
    assert len(h.cloud.calls) + len(h.local.calls) == calls + 1  # routed only: an idea is not news to check every open item against
    assert [i["text"] for i in h.engine.ideas.all()] == [IDEA]
    assert h.engine.undo_action(outcome.action_id)
    assert h.engine.ideas.all() == []


def test_in_privacy_routing_an_idea_is_discussed_on_the_local_model():
    """Privacy routing clears an item for the cloud by its domain; with routing "cloud" everything goes there anyway."""
    h = cloud_harness('{"domain": "jobs"}')  # the words alone would be cleared for the cloud
    idea = h.engine.ideas.add(IDEA)
    about = h.engine.about("idea", str(idea["n"]))
    assert about["title"] == IDEA and about["domain"] is None
    assert h.engine.about("idea", "99") is None and h.engine.about("idea", "x") is None
    c = h.engine.conversation()
    c.execute_turn(c.understand_turn(None, "這個要花多少時間?", {"kind": "idea", "id": str(idea["n"])}))
    assert h.cloud.calls == []  # no domain: nothing says the cloud may read it
    c.execute_turn(c.understand_turn(None, "這個要花多少時間?"))
    assert h.cloud.calls  # the same words outside the idea do go


def test_the_home_page_lists_ideas_newest_first():
    h = make_harness(at(2026, 10, 22, 10))
    h.engine.ideas.add("一個舊的點子")
    h.engine.ideas.add(IDEA)
    shown = h.engine.home_view()["ideas"]
    assert [i["text"] for i in shown] == [IDEA, "一個舊的點子"] and shown[0]["talk"] is None


def test_an_empty_idea_is_asked_about():
    h = make_harness(at(2026, 10, 22, 10))
    c = h.engine.conversation()
    route(h, "capture_idea", {"idea": " "})
    [outcome] = c.execute_turn(c.understand_turn(None, "有個點子")).outcomes
    assert "是什麼想法" in outcome.text and h.engine.ideas.all() == []


def talk_about(h, n, said, tool, args):
    route(h, tool, args)
    c = h.engine.conversation()
    return c.execute_turn(c.understand_turn(None, said, {"kind": "idea", "id": str(n)}))


def shown(h):
    return {i["n"]: i["status"] for i in h.engine.home_view()["ideas"]}


def test_an_idea_set_aside_stays_listed_a_dropped_one_leaves_and_both_come_back_on_undo():
    h = make_harness(at(2026, 10, 22, 10))
    a, b = h.engine.ideas.add("幫社區花園做澆水排程"), h.engine.ideas.add("學陶藝拉坯")
    # the model writes the idea's words where its number belongs: the one discussed is meant
    [aside] = talk_about(h, a["n"], "這個先放著", "settle_idea",
                         {"idea": "幫社區花園做澆水排程", "decision": "shelve", "why": "這個月太忙"}).outcomes
    assert aside.text == "想法「幫社區花園做澆水排程」先擱著" and shown(h) == {a["n"]: "shelved", b["n"]: "open"}
    [drop] = talk_about(h, b["n"], "算了不要了", "settle_idea", {"decision": "drop"}).outcomes
    assert shown(h) == {a["n"]: "shelved"}  # a dropped idea is decided: no longer listed
    assert h.engine.undo_action(drop.action_id) and h.engine.undo_action(aside.action_id)
    assert shown(h) == {a["n"]: "open", b["n"]: "open"}


def test_a_button_decides_as_saying_so_does(web):
    h, client, devices = web
    client.post("/api/pair", json={"code": devices.new_pairing_code()})
    n = h.engine.ideas.add("學陶藝拉坯")["n"]
    r = client.post(f"/api/ideas/{n}/settle", json={"decision": "shelved"}).json()
    assert r["text"] == "想法「學陶藝拉坯」先擱著" and r["action_id"]
    assert client.post(f"/api/ideas/{n}/settle", json={"decision": "open"}).json()["text"] == "想法「學陶藝拉坯」拿回來"


def test_an_idea_promoted_to_a_domain_leaves_the_list_and_comes_back_when_the_domain_is_undone():
    h = make_harness(at(2026, 10, 22, 10))
    n = h.engine.ideas.add("學陶藝拉坯")["n"]
    [made] = talk_about(h, n, "把這個變成正式的計畫,每週三小時,11/20 前訂到窯", "propose_domain",
                        {"id": "pottery", "title": "陶藝", "hours_per_week": 3, "why": "他說要每週練三小時",
                         "deadlines": [{"title": "訂到窯的時段", "due": "2026-11-20"}]}).outcomes
    assert made.text.startswith("建立「陶藝」這個領域;每週 3 小時,加上原本的")
    assert "每週目標合計" in made.text and "Go practice 6" in made.text  # what the new hours compete with
    assert h.engine.ideas.get(n)["status"] == "promoted" and n not in shown(h)
    review = {q["id"] for q in h.engine.review_queue() if q["domain"] == "pottery"}
    assert review == {"pottery/goal:weekly-hours", "pottery/deadline:deadline-1"}  # every drafted item waits for a yes
    plan = h.engine._plan("pottery", include_unconfirmed=True)
    assert plan.deadlines[0].source.startswith(f"idea:{n}:")  # what it is based on
    assert h.engine.undo_action(made.action_id)
    assert h.engine._plan("pottery", include_unconfirmed=True) is None and shown(h) == {n: "open"}


def test_a_domain_from_the_conversation_names_no_idea_and_asks_for_a_proper_id():
    h = make_harness(at(2026, 10, 22, 10))
    c = h.engine.conversation()
    route(h, "propose_domain", {"id": "Pottery Class", "title": "陶藝"})
    [bad] = c.execute_turn(c.understand_turn(None, "幫我開一個陶藝的領域")).outcomes
    assert "代號" in bad.text
    route(h, "propose_domain", {"id": "pottery", "title": "陶藝"})
    [made] = c.execute_turn(c.understand_turn(None, "幫我開一個陶藝的領域")).outcomes
    assert made.text.startswith("建立「陶藝」這個領域;目標和期限都在「計畫確認」等你一項一項說要") and made.action_id
    assert "哪些 repo、app 或地點的時間算這個領域" in made.text  # without them its hours stay at zero


def test_exploring_says_plainly_when_no_project_can_be_opened_and_names_it_when_one_is():
    h = make_harness(at(2026, 10, 22, 10))
    n = h.engine.ideas.add(IDEA)["n"]
    [cannot] = talk_about(h, n, "去查查看可不可行", "explore_idea", {}).outcomes
    assert "還不能開探討用的專案" in cannot.text and h.engine.ideas.get(n).get("sandbox") is None

    class OneSandbox:
        def create(self, name, purpose):
            return name

    h.engine.sandboxes = OneSandbox()
    [opened] = talk_about(h, n, "去查查看可不可行", "explore_idea", {}).outcomes
    assert opened.lend == "idea-1" and h.engine.about("idea", str(n))["explored_in"] == "idea-1"


def test_an_ideas_number_is_never_used_again():
    from conftest import at, make_harness

    h = make_harness(at(2026, 10, 22, 10))
    ideas = h.engine.ideas
    first = ideas.add("從照片做室內 3D")
    ideas.forget(first["n"])  # taken back (its sandbox, if any, keeps the name)
    assert ideas.add("學陶藝")["n"] == first["n"] + 1


class Sandboxes:
    def __init__(self):
        self.made = []

    def create(self, name, purpose):
        self.made.append((name, purpose))
        return name


def test_exploring_from_the_reply_that_kept_the_idea_opens_its_sandbox():
    """The reply keeps the idea and offers to explore it; the idea had no number when the offer was
    written, so pressing it must still find the one just kept."""
    h = make_harness(at(2026, 10, 24, 22))
    h.engine.sandboxes = sb = Sandboxes()
    c = h.engine.conversation()
    h.cloud.reply = h.local.reply = json.dumps({
        "actions": [{"tool": "capture_idea", "args": {"idea": IDEA}}], "reply": "可以試試。",
        "suggest": [{"label": "實際去查可行性", "tool": "explore_idea", "args": {}},
                    {"label": "先擱著", "tool": "settle_idea", "args": {"decision": "shelve"}}]}, ensure_ascii=False)
    r = c.execute_turn(c.understand_turn(None, "我想要用 2D 的照片重建我房間的 3D 模型,能做到嗎?"))
    [(tid, _)] = h.store.threads(10)
    assert r.outcomes[0].text.startswith("記下這個想法了")
    [opened] = c.act(tid, 0).outcomes
    assert opened.lend == "idea-1" and sb.made == [("idea-1", IDEA)]
    assert c.thread(tid)["suggest"] == []  # pressing one answers the round: the other offer goes
    c.execute_turn(c.understand_turn(None, "再一個:用照片量櫃子尺寸"))
    [(tid2, _), *_] = h.store.threads(10)
    [shelved] = c.act(tid2, 1).outcomes  # the other offer finds the idea just kept too
    assert not shelved.text.startswith("沒做成") and h.engine.ideas.get(2)["status"] == "shelved"
    assert c.thread(tid)["about"] == {"kind": "idea", "id": 1}  # the talk is now about the idea


def test_keeping_and_exploring_an_idea_in_one_go():
    h = make_harness(at(2026, 10, 24, 22))
    h.engine.sandboxes = sb = Sandboxes()
    c = h.engine.conversation()
    h.cloud.reply = h.local.reply = json.dumps({"actions": [{"tool": "capture_idea", "args": {"idea": IDEA}},
                                                            {"tool": "explore_idea", "args": {}}], "reply": ""}, ensure_ascii=False)
    kept, opened = c.execute_turn(c.understand_turn(None, "記下來然後去查查看")).outcomes
    assert opened.lend == "idea-1" and sb.made == [("idea-1", IDEA)]


def test_when_something_was_not_done_the_reply_written_before_is_not_said():
    from muninn.core.ports import SandboxRefused

    h = make_harness(at(2026, 10, 24, 22))
    h.engine.ideas._sandbox = lambda name, purpose: name

    def fails(name, rename):
        raise SandboxRefused("failed", detail="upgrade_failed")

    h.engine.ideas._upgrade = fails
    n = h.engine.ideas.capture(IDEA)[1]
    h.cloud.reply = h.local.reply = json.dumps({"actions": [{"tool": "promote_idea", "args": {"idea": n}}],
                                                "reply": "好,我把它升成專案了。"}, ensure_ascii=False)
    c = h.engine.conversation()
    r = c.execute_turn(c.understand_turn(None, "升級成專案", {"kind": "idea", "id": str(n)}))
    assert r.reply == "" and [o.text for o in r.outcomes][0].startswith("沒做成:升級成專案失敗了")
    assert "升成專案了" not in c.thread(h.store.threads(1)[0][0])["turns"][-1]["text"]


def test_a_promotion_while_the_session_is_mid_turn_happens_quietly_once_the_turn_is_done():
    from muninn.core.ports import SandboxRefused

    h = make_harness(at(2026, 10, 24, 22))
    ideas = h.engine.ideas
    ideas._sandbox = lambda name, purpose: name
    state = {"busy": True, "fail": False}

    def upgrade(name, rename):
        if state["busy"]:
            raise SandboxRefused("busy", detail="正在回答")
        if state["fail"]:
            raise SandboxRefused("failed", detail="upgrade_failed")
        return rename or name

    ideas._upgrade = upgrade
    n = ideas.capture(IDEA)[1]
    h.cloud.reply = h.local.reply = json.dumps({"actions": [{"tool": "promote_idea", "args": {"idea": n, "name": "shelf-planner"}}],
                                                "reply": "好。"}, ensure_ascii=False)
    c = h.engine.conversation()
    r = c.execute_turn(c.understand_turn(None, "升級成專案,叫 shelf-planner", {"kind": "idea", "id": str(n)}))
    assert [o.text for o in r.outcomes][0].startswith("好,它會變成正式專案,叫 shelf-planner") and not r.outcomes[0].failed
    assert ideas.get(n).get("status") != "promoted"
    locked = lambda fn: fn()  # noqa: E731
    assert ideas.retry_waiting(locked) == [] and ideas.get(n).get("status") != "promoted"  # still mid-turn: waits
    state["busy"] = False
    assert ideas.retry_waiting(locked) == []
    assert ideas.get(n)["status"] == "promoted" and ideas.get(n)["project"] == "shelf-planner"  # done, unseen
    assert ideas.retry_waiting(locked) == []  # nothing left waiting
    m = ideas.capture("另一個點子")[1]
    state["busy"] = True
    ideas.explore(m)
    try:
        ideas.promote(m, "other-thing")
    except Exception:
        pass
    state.update(busy=False, fail=True)
    h.engine.promote_waiting(locked)
    spoken = [t for t in h.engine.conversation().feed() if any(x.get("spoken") for x in t["turns"])]
    assert any("沒做成:升級成專案失敗了" in x["text"] for t in spoken for x in t["turns"])  # only a real failure is said


def test_an_idea_gets_a_name_to_know_it_by_from_the_moment_it_is_kept():
    h = make_harness(at(2026, 10, 25, 10))
    c = h.engine.conversation()
    route(h, "capture_idea", {"idea": "量沙發放不放得進客廳", "name": "Sofa-Fit"})
    [kept] = c.execute_turn(c.understand_turn(None, "有個點子:量沙發放不放得進客廳")).outcomes
    assert kept.text == "記下這個想法了:量沙發放不放得進客廳(叫它 sofa-fit)"
    ideas = h.engine.ideas
    assert ideas.add("另一個量沙發的", "sofa-fit")["name"] == "sofa-fit-2"  # taken: the next one free
    assert ideas.add("Plant Watering Reminder,澆花提醒")["name"] == "plant-watering-reminder"  # none given: from its words
    assert ideas.add("拍 3D 照片")["name"] == "idea-4"  # one latin word says too little: by its number
    assert ideas.add("取個怪名字", "沙發 fit")["name"] == "idea-5"  # not a name: never keeps it from being kept


def test_an_idea_is_renamed_by_saying_so_and_known_by_its_name():
    h = make_harness(at(2026, 10, 25, 10))
    ideas = h.engine.ideas
    n = ideas.add("量沙發放不放得進客廳", "sofa-fit")["n"]
    [renamed] = talk_about(h, n, "改叫 couch-check", "rename_idea", {"name": "couch-check"}).outcomes
    assert renamed.text == "好,改叫 couch-check。" and ideas.get(n)["name"] == "couch-check"
    [bad] = talk_about(h, n, "改叫 沙發", "rename_idea", {"name": "沙發"}).outcomes
    assert bad.failed and ideas.get(n)["name"] == "couch-check"
    assert ideas.by_name("couch-check") == n and ideas.by_name("我想接著聊 couch-check 那個") == n
    m = ideas.add("學陶藝", "pottery")["n"]
    assert ideas.by_name("pottery") == m and ideas.by_name("我今天去上 pottery 課") is None  # one word said in passing is not a name
    assert ideas.aka({"kind": "idea", "id": str(n)}) == f"couch-check, idea {n}"
    assert h.engine.about("idea", str(n))["name"] == "couch-check"


def test_a_sentence_naming_an_idea_goes_to_its_conversation():
    h = make_harness(at(2026, 10, 25, 10))
    n = h.engine.ideas.add("量沙發放不放得進客廳", "sofa-fit")["n"]
    got = h.engine.topics().choose(None, "接著聊 sofa-fit")
    assert got.about == {"kind": "idea", "id": str(n)} and got.title == "量沙發放不放得進客廳"


def test_its_sandbox_and_the_project_made_of_it_go_by_the_ideas_name():
    from muninn.core.ports import SandboxRefused

    h = make_harness(at(2026, 10, 25, 10))
    ideas = h.engine.ideas

    def numbers_only(name, purpose):  # a runner from before sandboxes could have names
        if not name.startswith("idea-"):
            raise SandboxRefused("bad_name")
        return name

    ideas._sandbox = numbers_only
    upgraded = []
    ideas._upgrade = lambda name, rename: upgraded.append((name, rename)) or rename or name
    n = ideas.add("量沙發放不放得進客廳", "sofa-fit")["n"]
    assert ideas.explore(n) == f"idea-{n}"  # that runner knows the sandbox by its number
    assert ideas.label_of(f"idea-{n}") == "sofa-fit" and ideas.label_of("garden") == "garden"
    [made] = talk_about(h, n, "升級成專案", "promote_idea", {}).outcomes
    assert upgraded == [(f"idea-{n}", "sofa-fit")]  # the project is called what the idea is called
    assert made.text.startswith("升級好了,叫 sofa-fit") and ideas.label_of("sofa-fit") == "sofa-fit"


def test_a_lent_talk_is_shown_and_spoken_of_by_the_ideas_name():
    from muninn.core.lease import ChannelLease
    from test_lease import FakeRunner, Threads

    h = make_harness(at(2026, 10, 25, 10))
    runner, threads = FakeRunner(), Threads()
    runner.sandboxes = ["idea-1"]
    leases = ChannelLease(h.store, h.clock, runner, h.channel, lambda now: False, threads.load, threads.update,
                          label={"idea-1": "sofa-fit"}.get, words=ZH)
    got = leases.lend(None, "idea-1")
    assert got["label"] == "sofa-fit" and "開了 sofa-fit 在試" in got["text"]
    [shown] = leases.active()
    assert shown["project"] == "idea-1" and shown["label"] == "sofa-fit"
    assert leases.shown(h.store.lease(got["lease_id"]))["label"] == "sofa-fit"


class Platform:
    """A sandbox side that opens sandboxes under any name but `refuse`, and renames them unless `busy`."""

    def __init__(self, refuse=()):
        self.made, self.moves, self.busy, self.refuse = [], [], False, set(refuse)

    def create(self, name, purpose):
        from muninn.core.ports import SandboxRefused

        if name in self.refuse:
            raise SandboxRefused("bad_name")
        if name in ("garden", "garden-2"):  # projects that are not ideas
            raise SandboxRefused("taken", detail=f"{name} exists")
        self.made.append(name)
        return name

    def upgrade(self, name, rename):
        return rename or name

    def rename(self, name, to):
        from muninn.core.ports import SandboxRefused

        if self.busy:
            raise SandboxRefused("busy", detail="正在回答")
        if to == "garden":
            raise SandboxRefused("taken", detail="garden exists")
        self.moves.append((name, to))
        return to


def test_a_sandbox_is_opened_under_the_ideas_name():
    h = make_harness(at(2026, 10, 25, 10))
    h.engine.sandboxes = p = Platform(refuse={"pottery-wheel"})
    ideas = h.engine.ideas
    n = ideas.add("量沙發放不放得進客廳", "sofa-fit")["n"]
    assert ideas.explore(n) == "sofa-fit" and p.made == ["sofa-fit"]
    m = ideas.add("學拉坏", "pottery-wheel")["n"]
    assert ideas.explore(m) == f"idea-{m}" and ideas.label_of(f"idea-{m}") == "pottery-wheel"  # refused that name: by its number
    g = ideas.add("陽台種菜", "garden")["n"]
    assert ideas.explore(g) == "garden-3" and ideas.get(g)["name"] == "garden-3"  # other projects have it: the next free one


def test_renaming_an_idea_renames_its_sandbox_where_it_lives_once_its_session_is_free():
    h = make_harness(at(2026, 10, 25, 10))
    h.engine.sandboxes = p = Platform()
    ideas = h.engine.ideas
    moved = []
    ideas._renamed = lambda old, new: moved.append((old, new))
    n = ideas.add("量沙發放不放得進客廳", "sofa-fit")["n"]
    ideas.explore(n)
    assert ideas.rename(n, "couch-check") == "couch-check"
    assert p.moves == [("sofa-fit", "couch-check")] and ideas.get(n)["sandbox"] == "couch-check"
    assert moved == [("sofa-fit", "couch-check")]  # what was lent to it goes on under the new name
    with pytest.raises(ValueError, match="被別的專案用掉"):
        ideas.rename(n, "garden")
    assert ideas.get(n)["name"] == "couch-check"  # refused there: still called what it was
    p.busy = True
    assert ideas.rename(n, "sofa-check") == "sofa-check" and ideas.label_of("couch-check") == "sofa-check"  # shown at once
    locked = lambda fn: fn()  # noqa: E731
    assert ideas.retry_waiting(locked) == [] and ideas.get(n)["sandbox"] == "couch-check"  # its session is mid-turn: waits
    p.busy = False
    assert ideas.retry_waiting(locked) == [] and ideas.get(n)["sandbox"] == "sofa-check"  # then done, unseen
    assert ideas.retry_waiting(locked) == [] and p.moves[-1] == ("couch-check", "sofa-check")


def test_renaming_a_project_made_of_an_idea_renames_the_project():
    h = make_harness(at(2026, 10, 25, 10))
    h.engine.sandboxes = p = Platform()
    ideas = h.engine.ideas
    n = ideas.add("量沙發放不放得進客廳", "sofa-fit")["n"]
    assert ideas.promote(n) == "sofa-fit"
    ideas.rename(n, "couch-check")
    assert p.moves == [("sofa-fit", "couch-check")] and ideas.get(n)["project"] == ideas.get(n)["sandbox"] == "couch-check"


def test_an_open_talk_shows_the_new_name_at_once_whether_or_not_its_session_is_free():
    from muninn.core.lease import ChannelLease
    from test_lease import FakeRunner, Threads

    h = make_harness(at(2026, 10, 25, 10))
    h.engine.sandboxes = p = Platform()
    ideas = h.engine.ideas
    n = ideas.add("量沙發放不放得進客廳", "sofa-fit")["n"]
    ideas.explore(n)
    runner, threads = FakeRunner(), Threads()
    runner.sandboxes = ["sofa-fit", "couch-check"]
    h.engine.leases = leases = ChannelLease(h.store, h.clock, runner, h.channel, lambda now: False, threads.load, threads.update,
                                            label=ideas.label_of, words=ZH)
    lid = leases.lend(None, "sofa-fit")["lease_id"]
    told = []
    leases.on_change(lambda lease_id, lease: told.append((lease_id, lease["project"], lease["label"])))
    ideas.rename(n, "couch-check")
    assert told == [(lid, "couch-check", "couch-check")]  # moved there, and shown by its new name
    p.busy = True
    ideas.rename(n, "sofa-check")
    assert told[-1] == (lid, "couch-check", "sofa-check")  # not moved yet, but already called so


def test_a_project_made_of_an_idea_is_not_set_aside_or_dropped_as_an_idea():
    h = make_harness(at(2026, 10, 25, 10))
    h.engine.sandboxes = Platform()
    ideas = h.engine.ideas
    n = ideas.add("量沙發放不放得進客廳", "sofa-fit")["n"]
    ideas.promote(n)
    [refused] = talk_about(h, n, "今天先這樣", "settle_idea", {"decision": "shelve", "why": "今天先收回"}).outcomes
    assert refused.failed and "已經是正式專案" in refused.text and ideas.get(n)["status"] == "promoted"
