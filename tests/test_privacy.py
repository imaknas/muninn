"""Sensitive data never reaches the cloud LLM adapter."""

from __future__ import annotations

import json

import pytest

from conftest import CANARY, at, make_harness
from muninn.core.llm_router import CloudSafe, LLMRequest, LLMRouter
from muninn.core.models import Observation, Sensitivity


def all_cloud_text(h) -> str:
    return "\n".join(s + "\n" + p for s, p in h.cloud.calls)


def sensitive_observations(now):
    return [
        # attributed to the always-sensitive work domain
        Observation("git", now, "commit", "git:work-api:1", {"repo": "work-api", "secret": CANARY}, sensitivity=Sensitivity.PERSONAL),
        # unattributed
        Observation("git", now, "commit", "git:mystery:1", {"repo": "mystery", "secret": CANARY}, sensitivity=Sensitivity.PERSONAL),
        # explicitly sensitive, even though the repo maps to a personal domain
        Observation("aw", now, "window", "aw:1", {"repo": "resume", "title": CANARY}, sensitivity=Sensitivity.SENSITIVE),
    ]


def test_full_day_sends_nothing_sensitive_to_cloud():
    h = make_harness(at(2026, 10, 20, 9))
    h.engine.ingest(sensitive_observations(h.clock.now()))
    h.engine.add_note(f"private note {CANARY}", None)
    h.engine.add_note(f"jobs note {CANARY}", "jobs")
    h.engine.tick()  # includes local classification of unattributed repos
    h.engine.morning()
    h.clock.t = at(2026, 10, 20, 22, 30)
    h.engine.evening()
    h.engine.weekly()

    # the evening summary did use the cloud, with whitelisted fields only
    assert h.cloud.calls, "expected the non-sensitive evening wording to go to the cloud"
    text = all_cloud_text(h)
    assert CANARY not in text
    for forbidden in ("pivot", "decision_tree", "notes", "filters", "work-api", "mystery", "example.com"):
        assert forbidden not in text
    # every cloud payload is JSON from privacy.evening_payload, for a non-sensitive domain
    for _, prompt in h.cloud.calls:
        payload = json.loads(prompt)
        assert payload["domain"] in {"jobs", "health"}
    # the unattributed repo was classified locally only
    assert any("mystery" in p for _, p in h.local.calls)


def test_work_domain_never_reaches_cloud_even_as_cloud_safe():
    h = make_harness(at(2026, 10, 20, 22, 30))
    h.engine.ingest(sensitive_observations(h.clock.now()))
    h.engine.evening()
    assert all(json.loads(p)["domain"] != "work" for _, p in h.cloud.calls)


def make_router(cloud_up=True, local_up=True):
    from conftest import RecordingLLM

    cloud = RecordingLLM("cloud", False, up=cloud_up)
    local = RecordingLLM("local", True, up=local_up)
    return LLMRouter([cloud, local], ["work"]), cloud, local


def cloud_safe(text: str) -> CloudSafe:
    from muninn.core.llm_router import _make_cloud_safe

    return _make_cloud_safe(text)


@pytest.mark.parametrize(
    "req",
    [
        LLMRequest("s", "plain string", Sensitivity.PUBLIC, "jobs"),  # raw strings never go to the cloud
        LLMRequest("s", cloud_safe("x"), Sensitivity.SENSITIVE, "jobs"),
        LLMRequest("s", cloud_safe("x"), Sensitivity.PERSONAL, None),  # unattributed
        LLMRequest("s", cloud_safe("x"), Sensitivity.PERSONAL, "work"),  # always-sensitive domain
    ],
)
def test_router_keeps_these_local(req):
    router, cloud, local = make_router()
    assert router.complete(req) == "ok"
    assert cloud.calls == [] and len(local.calls) == 1


@pytest.mark.parametrize(
    "req",
    [
        LLMRequest("s", f"sensitive {CANARY}", Sensitivity.SENSITIVE, None),
        LLMRequest("s", cloud_safe(CANARY), Sensitivity.SENSITIVE, "jobs"),
    ],
)
def test_local_down_means_skip_never_cloud(req):
    router, cloud, local = make_router(local_up=False)
    assert router.complete(req) is None
    assert cloud.calls == []


def test_cloud_allowed_only_for_cloud_safe_non_sensitive():
    router, cloud, local = make_router()
    assert router.complete(LLMRequest("s", cloud_safe("progress"), Sensitivity.PERSONAL, "jobs")) == "ok"
    assert len(cloud.calls) == 1 and local.calls == []


def test_cloud_safe_cannot_be_built_directly():
    with pytest.raises(TypeError):
        CloudSafe("anything")


def test_failing_local_backend_does_not_fall_back_to_cloud():
    router, cloud, local = make_router()

    def boom(*a, **k):
        raise RuntimeError("down")

    local.complete = boom
    assert router.complete(LLMRequest("s", "x", Sensitivity.SENSITIVE, None)) is None
    assert cloud.calls == []


def test_private_goals_and_deadlines_are_left_out():
    from dataclasses import replace

    h = make_harness(at(2026, 10, 22, 22, 30))
    jobs = h.plans.plans["jobs"]
    h.plans.plans["jobs"] = replace(
        jobs,
        goals=tuple(replace(g, private=True) if g.id == "golang" else g for g in jobs.goals),
        deadlines=tuple(replace(d, private=True) if d.id == "call-bank" else d for d in jobs.deadlines),
    )
    h.engine.evening()
    text = all_cloud_text(h)
    assert "Go practice" not in text and "Call the bank" not in text
    assert "Weekly applications" in text
