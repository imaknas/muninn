from __future__ import annotations

import json

from conftest import at, make_harness


def test_a_new_users_first_words_become_their_first_domains():
    h = make_harness(at(2026, 10, 22, 10), plans=[])
    topic = h.engine.about("onboarding", "first")
    assert topic["domains_now"] == [] and "propose_domain" in topic["note"]
    c = h.engine.conversation()
    h.cloud.reply = h.local.reply = json.dumps({"actions": [
        {"tool": "propose_domain", "args": {"id": "job-search", "title": "換工作", "hours_per_week": 6,
                                            "deadlines": [{"title": "投出第一批履歷", "due": "2026-11-15"}], "why": "他說年底前想換工作"}},
        {"tool": "propose_domain", "args": {"id": "health", "title": "健康", "hours_per_week": None, "deadlines": [], "why": "他說想多運動"}},
    ], "reply": "幫你開了兩個領域;健康每週大概想花幾小時?"}, ensure_ascii=False)
    turn = c.understand_turn(None, "我最近在意的是:年底前想換工作,還有想多運動", {"kind": "onboarding", "id": "first"})
    prompts = [s + p for s, p in h.cloud.calls + h.local.calls if "propose_domain" in s + p and "建立第一個領域" in s + p]
    assert prompts  # the model was told this is a new user's first setup
    c.execute_turn(turn)
    assert {p.id for p in h.engine.plans.load()} == {"job-search", "health"}
    assert h.engine.about("onboarding", "first")["domains_now"] == ["換工作", "健康"]  # not opened twice
    assert any(x["domain"] == "job-search" for x in h.engine.review_queue())  # every drafted item waits for a yes
