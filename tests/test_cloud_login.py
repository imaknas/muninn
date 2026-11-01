"""A lapsed cloud login fails the request and tells the user; any other cloud failure is
answered by the local model, and the answer says so."""

from __future__ import annotations

import json
import stat

import pytest

from conftest import ZH, RecordingLLM, at, make_harness
from muninn.adapters.llm.claude_cli import ClaudeCliBackend
from muninn.core.llm_router import CloudFirst, LLMRequest, LLMRouter, PrivacyFirst
from muninn.core.models import Sensitivity
from muninn.core.ports import CloudLoginLost
from muninn.core.understanding import Understanding

ROUTE = json.dumps({"actions": [], "reply": "好"})


class FailingCloud(RecordingLLM):
    def __init__(self, error: Exception):
        super().__init__("claude-cli:sonnet", False, label="雲端甲", login_hint="在雲端甲的設定頁重新登入")
        self.error = error

    def complete(self, system, prompt, *, json_mode=False):
        self.calls.append((system, prompt))
        raise self.error


def request(task="route"):
    return LLMRequest("s", "c", Sensitivity.SENSITIVE, None, task=task)


def test_a_lapsed_login_is_not_answered_by_the_local_model():
    lost = []
    local = RecordingLLM("ollama", True, reply="local guess")
    router = LLMRouter([FailingCloud(CloudLoginLost("Invalid API key · Please run /login")), local], [], CloudFirst(),
                       on_login_lost=lambda backend, why: lost.append((backend.name, why)))
    c = router.complete_case(request())
    assert c.reply is None and c.login_lost and not c.stood_in
    assert local.calls == [] and lost == [("claude-cli:sonnet", "Invalid API key · Please run /login")]


def test_under_cloud_routing_no_local_model_answers_when_the_cloud_fails():
    local = RecordingLLM("ollama", True, reply="local answer")
    router = LLMRouter([FailingCloud(TimeoutError("120s")), local], [], CloudFirst())
    c = router.complete_case(request())
    assert (c.reply, c.backend, c.stood_in, c.login_lost) == (None, None, False, None) and c.unanswered
    assert local.calls == []


def test_under_privacy_routing_a_local_model_stands_in_and_is_marked():
    local = RecordingLLM("ollama", True, reply="local answer")
    router = LLMRouter([FailingCloud(TimeoutError("120s")), local], [], PrivacyFirst())
    from muninn.core.privacy import UNDERSTANDING_DOMAIN, Cleared, understanding_payload

    cleared = LLMRequest("s", understanding_payload({"said": "c"}, Cleared("c")), Sensitivity.PERSONAL, UNDERSTANDING_DOMAIN, task="route")
    c = router.complete_case(cleared)
    assert (c.reply, c.backend, c.stood_in, c.login_lost) == ("local answer", "ollama", True, None)


def test_a_request_meant_for_the_local_model_is_not_a_stand_in():
    local = RecordingLLM("ollama", True, reply="local answer")
    c = LLMRouter([RecordingLLM("claude-cli:haiku", False), local], [], PrivacyFirst()).complete_case(request())
    assert c.backend == "ollama" and not c.stood_in


@pytest.mark.parametrize("code,out,lost", [
    (1, "Invalid API key · Please run /login", True),
    (1, 'API Error: 401 {"type":"error","error":{"type":"authentication_error"}}', True),
    (0, "OAuth token has expired. Please obtain a new token or refresh your existing token.", True),
    (0, "401", False),  # a short reply may well be a number
    (1, "Error: overloaded", False),
])
def test_the_cli_says_when_its_login_lapsed(tmp_path, code, out, lost):
    cli = tmp_path / "claude"
    cli.write_text(f"#!/bin/sh\ncat > /dev/null\necho '{out}'\nexit {code}\n")
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    backend = ClaudeCliBackend(tmp_path / "work", binary=str(cli), words=ZH)
    if lost:
        with pytest.raises(CloudLoginLost):
            backend.complete("s", "p")
    elif code:
        with pytest.raises(RuntimeError):
            backend.complete("s", "p")
    else:
        assert backend.complete("s", "p") == out


def harness(cloud):
    h = make_harness(at(2026, 10, 22, 10))
    h.local.reply = ROUTE
    h.engine.understanding = Understanding(LLMRouter([cloud, h.local], [], CloudFirst(), on_login_lost=h.engine.cloud_login_lost))
    return h


def test_a_turn_during_a_lapsed_login_says_so_and_pushes_once():
    h = harness(FailingCloud(CloudLoginLost("Please run /login")))
    c = h.engine.conversation()
    r = c.execute_turn(c.understand_turn(None, "明天下午有空嗎"))
    assert "雲端甲 的登入失效了" in r.reply and r.outcomes == [] and h.local.calls == []  # named as the backend calls itself
    c.execute_turn(c.understand_turn(None, "那後天呢"))
    pushed = [p for p in h.channel.sent if "雲端甲 的登入失效了" in json.dumps(p, ensure_ascii=False, default=str)]
    assert len(pushed) == 1  # one push, not one per request
    notes = h.store.observations(at(2026, 10, 22, 0), kinds=("note",))
    assert [n.payload["text"] for n in notes] == ["明天下午有空嗎", "那後天呢"]  # the words are kept


def test_a_turn_no_model_answered_says_so_and_keeps_the_words(web):
    h, client, devices = web
    client.post("/api/pair", json={"code": devices.new_pairing_code()})
    h.local.reply = ROUTE
    h.engine.understanding = Understanding(LLMRouter([FailingCloud(TimeoutError("120s")), h.local], [], CloudFirst()))
    r = client.post("/api/command", json={"text": "今天好累"}).json()
    assert "沒有模型回應" in r["reply"] and not r["stood_in"] and r["understood"] is False
    assert h.local.calls == []  # no local model guesses in the cloud's place
    notes = h.store.observations(at(2026, 10, 22, 0), kinds=("note",))
    assert [n.payload["text"] for n in notes] == ["今天好累"]


def test_what_wrote_something_is_named_as_its_backend_calls_itself():
    router = LLMRouter([RecordingLLM("prov:small", False, label="雲端甲"), RecordingLLM("ollama:x", True, label="本機模型")], [])
    assert router.label_of("prov:small") == "雲端甲"
    assert router.label_of("prov:large") == "雲端甲"  # another model of the same provider, never used here yet
    assert router.label_of("ollama:x") == "本機模型"
    assert router.label_of("gone:model") == "gone:model" and router.label_of(None) == ""
