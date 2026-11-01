from __future__ import annotations

import json

from conftest import at, make_harness
from muninn.core.understanding import Understanding, WorkCheck

ROUTE = json.dumps({"actions": [], "reply": "好"}, ensure_ascii=False)


def harness(work_reply: str | None):
    h = make_harness(at(2026, 10, 21, 21))
    router = h.engine.router
    h.engine.understanding = Understanding(router, WorkCheck(router, {"work"}, lambda: [("work", "Work"), ("jobs", "Jobs")], [r"(?i)acme"]))

    def local(system, prompt, *, json_mode=False):
        h.local.calls.append((system, prompt))
        return work_reply if "life domains" in system else ROUTE

    def cloud(system, prompt, *, json_mode=False):
        h.cloud.calls.append((system, prompt))
        return ROUTE

    h.local.complete, h.cloud.complete = local, cloud
    return h


def test_cleared_words_go_to_the_cloud_without_work_context():
    h = harness('{"domain": "jobs"}')
    h.engine.command("今天好累")
    assert len(h.local.calls) == 1  # only the work check
    assert len(h.cloud.calls) == 2  # routing, then what the remark settles
    for _, prompt in h.cloud.calls:
        assert "今天好累" in prompt and '"work/' not in prompt and "Work" not in prompt
    case, = h.store.eval_cases("route")
    assert case["backend"] == "cloud" and case["cloud_ok"]


def test_work_words_stay_local():
    for reply in ('{"domain": "work"}', None, "not json", '{"domain": "made-up"}'):
        h = harness(reply)
        h.engine.command("客戶說 deadline 改了")
        assert h.cloud.calls == [], reply
        assert h.store.eval_cases("route")[0]["backend"] == "local"


def test_work_patterns_skip_the_model_entirely():
    h = harness('{"domain": "jobs"}')
    h.engine.command("acme 的 PR 合了")
    assert h.cloud.calls == [] and not any("life domains" in s for s, _ in h.local.calls)


def test_without_work_check_everything_is_local():
    h = make_harness(at(2026, 10, 21, 21))
    h.local.reply = ROUTE
    h.engine.command("今天好累")
    assert h.cloud.calls == []
