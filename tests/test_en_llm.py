"""What the model adapters tell an English user (adapters/llm/): what a model is called, how a lapsed login is put
right, and a prompt too long to send."""

from __future__ import annotations

import re

import pytest

from muninn.adapters.llm.anthropic_api import AnthropicApiProvider
from muninn.adapters.llm.claude_cli import ClaudeCliBackend
from muninn.adapters.llm.keys import EnvKey, SecretFile
from muninn.adapters.llm.ollama import OllamaBackend, OllamaProvider
from muninn.adapters.llm.openai_compat import OpenAICompatProvider
from muninn.languages import language

EN = language("en")
HAN = re.compile(r"[一-鿿]")


def test_the_local_model_and_a_compatible_service_are_named_in_english():
    assert OllamaProvider("home", words=EN).backend("qwen3:8b").label == "local model"
    assert OllamaProvider.label(EN) == "local model" and OpenAICompatProvider.label(EN) == "OpenAI-compatible"


def test_how_to_put_a_lapsed_login_right_is_said_in_english(tmp_path):
    from_env = AnthropicApiProvider("anthropic", EnvKey("INVENTED_API_KEY", {}, words=EN), words=EN).backend("claude-haiku-5-5")
    assert from_env.login_hint == "Put the API key for Anthropic API in the environment variable INVENTED_API_KEY"
    from_file = AnthropicApiProvider("anthropic", SecretFile(tmp_path / "key"), words=EN).backend("claude-haiku-5-5")
    assert from_file.login_hint == f"Put the API key for Anthropic API in {tmp_path / 'key'}"
    cli = ClaudeCliBackend(tmp_path / "work", words=EN)
    assert cli.login_hint == "Run claude on this computer and log in again with /login"
    relay = OpenAICompatProvider("relay", "https://relay.example/v1", EnvKey("INVENTED_RELAY_KEY", {}, words=EN), words=EN).backend("m")
    assert HAN.search(from_env.login_hint + from_file.login_hint + cli.login_hint + relay.login_hint) is None


def test_a_prompt_too_long_for_the_model_is_said_in_english():
    with pytest.raises(ValueError, match="Prompt too long: about") as e:
        OllamaBackend("tiny", context=100, words=EN).complete("s", "word " * 2000)
    assert HAN.search(str(e.value)) is None
