"""Ollama and OpenAI-compatible services as providers, the local model among them, over their real request shapes
(answered here instead of the network)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from muninn.adapters.llm.ollama import OllamaProvider
from muninn.adapters.llm.openai_compat import OpenAICompatProvider
from muninn.core.models import Attachment
from muninn.core.ports import Capabilities, CloudLoginLost
from muninn.llm_config import ProviderContext, llm_setup, private
from muninn.main import IMPLS

from conftest import ZH

KEY = "sk-invented-compatible-key"


class Service:
    """Answers the provider's requests with what each test says, keeping what was sent."""

    def __init__(self, *answers):
        self.answers, self.sent, self.slept = list(answers), [], []

    def post(self, url, json=None, headers=None, timeout=None):
        self.sent.append((url, json, headers or {}))
        status, body, extra = (self.answers.pop(0) + ({},))[:3]
        return httpx.Response(status, json=body, headers=extra, request=httpx.Request("POST", url))

    def get(self, url, headers=None, timeout=None):
        status, body, _ = (self.answers.pop(0) + ({},))[:3]
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))


def chat(text, finish="stop"):
    return 200, {"choices": [{"message": {"content": text}, "finish_reason": finish}], "usage": {"prompt_tokens": 9, "completion_tokens": 4}}


class Key:
    where = "secrets/openrouter-key"

    def read(self):
        return KEY


def compat(service, **kw):
    return OpenAICompatProvider("openrouter", "https://openrouter.example/api/v1", Key(), post=service.post, get=service.get,
                                sleep=service.slept.append, words=ZH, **kw)


def test_a_compatible_service_gets_the_key_the_model_and_json_asked_for():
    s = Service(chat('<think>hm</think>{"ok": true}'))
    b = compat(s, label="OpenRouter").backend("deepseek/deepseek-chat")
    got = b.complete("回答", "hello", json_mode=True)
    url, body, headers = s.sent[0]
    assert url == "https://openrouter.example/api/v1/chat/completions" and headers["Authorization"] == f"Bearer {KEY}"
    assert body["model"] == "deepseek/deepseek-chat" and body["response_format"] == {"type": "json_object"}
    assert "JSON" in body["messages"][0]["content"]  # the word response_format wants is in the messages
    assert got == '{"ok": true}' and (got.input_tokens, got.output_tokens) == (9, 4)  # thinking set aside
    assert (b.name, b.label, b.is_local) == ("openrouter:deepseek/deepseek-chat", "OpenRouter", False)


def test_a_service_that_refuses_response_format_is_asked_in_the_prompt_from_then_on():
    s = Service((400, {"error": {"message": "response_format is not supported"}}), chat("{}"), chat("{}"))
    b = compat(s).backend("m")
    assert b.complete("s", "p", json_mode=True) == "{}"
    assert "response_format" not in s.sent[1][1] and s.sent[1][1]["messages"][0]["content"].count("JSON object") == 1
    b.complete("s", "p", json_mode=True)
    assert "response_format" not in s.sent[2][1]  # remembered


def test_a_cut_off_or_filtered_reply_is_a_failure_and_a_rejected_key_a_lapsed_login():
    for answer, says in ((chat("half", "length"), "cut off"), (chat("", "content_filter"), "content filter")):
        with pytest.raises(RuntimeError, match=says):
            compat(Service(answer)).backend("m").complete("s", "p")
    with pytest.raises(CloudLoginLost):
        compat(Service((401, {"error": "bad key"}))).backend("m").complete("s", "p")


def test_a_service_that_cannot_do_it_is_not_asked_again():
    s = Service((501, {"error": "not implemented"}))
    with pytest.raises(httpx.HTTPStatusError):
        compat(s).backend("m").complete("s", "p")
    assert len(s.sent) == 1 and s.slept == []


def test_rate_limits_are_waited_out_a_few_times():
    s = Service((429, {}, {"retry-after": "1"}), (503, {}), chat("ok"))
    assert compat(s).backend("m").complete("s", "p") == "ok" and s.slept == [1.0, 4]


def test_photos_go_as_data_urls_only_to_a_model_said_to_read_them():
    s = Service(chat("客廳"))
    p = compat(s, reads={"gpt-vision": Capabilities(images=True)})
    assert p.backend("gpt-text").capabilities == Capabilities()
    p.backend("gpt-vision").complete("s", "這是哪裡?", attachments=(Attachment("room.png", "image/png", b"png"),))
    parts = s.sent[0][1]["messages"][1]["content"]
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,") and parts[-1] == {"type": "text", "text": "這是哪裡?"}


def test_the_check_finds_the_model_in_the_services_list_or_says_it_cannot():
    assert compat(Service((200, {"data": [{"id": "m"}]}))).backend("m").check() == "listed"
    with pytest.raises(RuntimeError, match="not among"):
        compat(Service((200, {"data": [{"id": "other"}]}))).backend("m").check()
    assert "no model list" in compat(Service((404, {}))).backend("m").check()


def test_a_prompt_longer_than_the_model_reads_is_not_sent():
    s = Service(chat("never"))
    with pytest.raises(ValueError, match="prompt 太長"):
        compat(s, contexts={"small": 100}).backend("small").complete("s", "字" * 200)
    assert s.sent == []


def test_ollama_reads_as_much_as_declared_and_sees_photos_only_when_said_to(monkeypatch):
    sent = []
    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: (sent.append(json), httpx.Response(
        200, json={"message": {"content": "<think>x</think>好"}, "done_reason": "stop", "prompt_eval_count": 5, "eval_count": 1},
        request=httpx.Request("POST", url)))[1])
    p = OllamaProvider("local", contexts={"qwen2.5vl:7b": 32768}, reads={"qwen2.5vl:7b": Capabilities(images=True)}, words=ZH)
    b = p.backend("qwen2.5vl:7b")
    got = b.complete("s", "看圖", attachments=(Attachment("a.png", "image/png", b"png"),))
    assert got == "好" and sent[0]["options"]["num_ctx"] == 32768 and sent[0]["messages"][1]["images"] == ["cG5n"]
    assert b.is_local and b.label == "本機模型" and b.capabilities.images and not b.capabilities.pdf


def test_the_older_local_model_setting_keeps_its_name_and_label():
    setup = llm_setup({"cloud": "claude-cli", "local_model": "qwen3.6:35b-mlx", "ollama_url": "http://127.0.0.1:11434"}, IMPLS)
    assert setup.local.name == "ollama" and setup.local.models["fast"] == "qwen3.6:35b-mlx" and setup.use["fast"] == "claude"
    b = OllamaProvider.from_spec(setup.local, ProviderContext(Path(), Path(), words=ZH)).backend("qwen3.6:35b-mlx")
    assert (b.name, b.label, b.is_local) == ("ollama:qwen3.6:35b-mlx", "本機模型", True)  # as recorded cases know it


def test_a_local_provider_is_the_local_model_and_only_that():
    llm = {"providers": {"lan": {"impl": "openai", "location": "local", "base_url": "http://192.168.1.20:1234/v1",
                                 "models": {"fast": {"name": "qwen3-8b", "context": 32768}}},
                         "anthropic": {"impl": "anthropic", "key": {"secret": "k"}}}}
    setup = llm_setup(llm, IMPLS)
    assert setup.local.name == "lan" and setup.local.contexts == {"qwen3-8b": 32768} and setup.use["fast"] == "anthropic"
    with pytest.raises(ValueError, match="local provider"):
        llm_setup({**llm, "points": {"why": {"provider": "lan", "model": "qwen3-8b"}}}, IMPLS).task_models()
    with pytest.raises(ValueError, match="both name the local model"):
        llm_setup({**llm, "local_model": "qwen3:8b"}, IMPLS)


@pytest.mark.parametrize("url, ok", [("http://127.0.0.1:11434", True), ("http://localhost:1234/v1", True),
                                     ("http://192.168.1.20:1234", True), ("http://100.101.102.103:11434", True),
                                     ("https://box.tail1234.ts.net", True), ("http://host.docker.internal:11434", True),
                                     ("https://api.openai.com/v1", False), ("http://8.8.8.8", False)])
def test_what_counts_as_this_machine_or_network(url, ok):
    assert private(url) is ok


def test_the_models_offered_can_chat_and_the_newest_comes_first():
    import httpx

    from conftest import ZH
    from muninn.adapters.llm.openai_compat import OpenAICompatProvider

    listed = {"data": [{"id": "babbage-002", "created": 1}, {"id": "text-embedding-3-large", "created": 9},
                       {"id": "gpt-old", "created": 2}, {"id": "whisper-1", "created": 8}, {"id": "gpt-new", "created": 7}]}

    def get(url, **kw):
        return httpx.Response(200, json=listed, request=httpx.Request("GET", url))

    p = OpenAICompatProvider("openai", "https://api.example/v1", None, get=get, words=ZH)
    assert p.models() == ["gpt-new", "gpt-old"]
    listed["data"] = [{"id": "nomic-embed-text"}]  # only what looks unfit: still offered, the check at saving decides
    assert p.models() == ["nomic-embed-text"]
