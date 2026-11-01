"""How the Claude CLI is found and logs in."""

from __future__ import annotations

import stat

import pytest

from conftest import ZH, RecordingLLM
from muninn.adapters.llm.claude_cli import TOKEN_VAR, ClaudeCliBackend, HeadlessToken, OwnLogin, find_claude
from muninn.core.llm_router import CloudFirst, LLMRequest, LLMRouter
from muninn.core.models import Sensitivity
from muninn.core.ports import CloudLoginLost

TOKEN = "invented-token-for-tests"


def script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\ncat > /dev/null\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def test_the_token_is_read_on_every_call_and_handed_over_in_the_environment(tmp_path):
    cli = script(tmp_path / "claude", f'printf "%s" "${TOKEN_VAR}"')
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n")
    backend = ClaudeCliBackend(tmp_path / "work", binary=str(cli), credentials=HeadlessToken(token), words=ZH)
    assert backend.complete("s", "p") == TOKEN
    token.write_text("renewed")  # replaced while running: the next call has the new one
    assert backend.complete("s", "p") == "renewed"


@pytest.mark.parametrize("content", [None, "", "  \n"])
def test_no_token_to_give_is_a_lapsed_login(tmp_path, content):
    token = tmp_path / "token"
    if content is not None:
        token.write_text(content)
    with pytest.raises(CloudLoginLost):
        HeadlessToken(token).env()


def test_with_no_token_nothing_is_answered_in_its_place(tmp_path):
    cli = script(tmp_path / "claude", "echo should-not-run")
    lost, local = [], RecordingLLM("ollama", True, reply="local guess")
    cloud = ClaudeCliBackend(tmp_path / "work", binary=str(cli), credentials=HeadlessToken(tmp_path / "missing"), words=ZH)
    router = LLMRouter([cloud, local], [], CloudFirst(), on_login_lost=lambda b, why: lost.append(why))
    c = router.complete_case(LLMRequest("s", "c", Sensitivity.SENSITIVE, None, task="route"))
    assert c.login_lost and c.reply is None and local.calls == []
    assert lost and TOKEN not in lost[0]


def test_its_own_login_ignores_a_token_left_in_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv(TOKEN_VAR, TOKEN)
    cli = script(tmp_path / "claude", f'printf "[%s]" "${TOKEN_VAR}"')
    assert ClaudeCliBackend(tmp_path / "work", binary=str(cli), credentials=OwnLogin(), words=ZH).complete("s", "p") == "[]"


def test_the_cli_is_found_under_the_newest_nvm_node_when_the_configured_one_is_gone(tmp_path):
    nvm = tmp_path / "nvm"
    for v in ("v22.1.0", "v24.9.0", "v24.19.0"):
        script(nvm / "versions" / "node" / v / "bin" / "claude", "echo ok")
    (nvm / "versions" / "node" / "v25.0.0" / "bin").mkdir(parents=True)  # a newer node without the CLI
    gone = str(nvm / "versions" / "node" / "v23.0.0" / "bin" / "claude")
    assert find_claude(gone, nvm) == str(nvm / "versions" / "node" / "v24.19.0" / "bin" / "claude")
    here = script(tmp_path / "bin" / "claude", "echo ok")
    assert find_claude(str(here), nvm) == str(here)  # the configured one, while it is there
    assert find_claude("no-such-claude-cli", None) is None
    backend = ClaudeCliBackend(tmp_path / "work", binary="no-such-claude-cli", nvm_dir=tmp_path / "empty", words=ZH)
    assert not backend.available()  # not found: the router goes on to the local model, marked


def test_a_call_is_sealed_off_from_the_users_mcp_servers_hooks_and_memory(tmp_path):
    import json

    cli = script(tmp_path / "claude", 'printf "%s\\n" "$@"')
    token = tmp_path / "token"
    token.write_text(TOKEN)
    args = ClaudeCliBackend(tmp_path / "work", binary=str(cli), credentials=HeadlessToken(token), words=ZH).complete("s", "p").split("\n")
    assert "--strict-mcp-config" in args and args[args.index("--tools") + 1] == ""
    settings = json.loads(args[args.index("--settings") + 1])
    assert settings["disableAllHooks"] is True and settings["autoMemoryEnabled"] is False
