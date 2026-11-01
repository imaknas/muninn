"""The Codex CLI as a model provider: always sealed, its reply and tokens read from its events, a lapsed login said."""

from __future__ import annotations

import json
import stat

import pytest

from conftest import ZH
from muninn.adapters.llm.codex_cli import TOOLS_OFF, CodexCliBackend, CodexCliProvider
from muninn.core.models import Attachment
from muninn.core.ports import CloudLoginLost

EVENTS = [{"type": "thread.started"}, {"type": "turn.started"},
          {"type": "item.completed", "item": {"id": "i0", "type": "agent_message", "text": "Taipei."}},
          {"type": "turn.completed", "usage": {"input_tokens": 4800, "output_tokens": 12}}]


def fake_codex(tmp_path, events=EVENTS, code=0, stderr=""):
    """A `codex` that keeps its arguments, the instructions file it was given and its stdin, then prints `events`."""
    out = "\n".join(json.dumps(e) for e in events)
    cli = tmp_path / "bin" / "codex"
    cli.parent.mkdir(parents=True)
    cli.write_text(f"""#!/bin/sh
printf '%s\\n' "$@" > {tmp_path}/argv
cat > {tmp_path}/stdin
for a in "$@"; do case "$a" in model_instructions_file=*) f=$(printf '%s' "${{a#model_instructions_file=}}" | tr -d '"'); cp "$f" {tmp_path}/instructions;; esac; done
printf '%s\\n' '{out}'
printf '%s' '{stderr}' >&2
exit {code}
""")
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    return cli


def backend(tmp_path, cli, **kw):
    return CodexCliBackend(tmp_path / "work", binary=str(cli), words=ZH, **kw)


def test_every_call_is_sealed_and_says_what_it_took(tmp_path):
    cli = fake_codex(tmp_path)
    r = backend(tmp_path, cli).complete("You are Muninn.", "What is the capital of Taiwan?")
    assert (str(r), r.input_tokens, r.output_tokens) == ("Taipei.", 4800, 12)
    argv = (tmp_path / "argv").read_text().splitlines()
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "--json", 'web_search="disabled"'):
        assert flag in argv
    assert argv[argv.index("-s") + 1] == "read-only"
    assert {argv[i + 1] for i, a in enumerate(argv) if a == "--disable"} == set(TOOLS_OFF)
    assert "-m" not in argv  # no model of its own: Codex's default
    assert (tmp_path / "instructions").read_text() == "You are Muninn."  # in place of Codex's own
    assert (tmp_path / "stdin").read_text() == "What is the capital of Taiwan?"
    assert list((tmp_path / "work").iterdir()) == []  # nothing left behind


def test_a_model_and_photos_go_along_and_another_providers_key_does_not(tmp_path, monkeypatch):
    cli = fake_codex(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "invented-key")
    cli.write_text(cli.read_text().replace("exit 0", 'printf "%s" "$OPENAI_API_KEY" > ' + str(tmp_path) + "/key\nexit 0"))
    photo = Attachment("pot.jpg", "image/jpeg", b"\xff\xd8 invented")
    backend(tmp_path, cli, model="gpt-6-luna", scrub=["OPENAI_API_KEY"]).complete("s", "p", attachments=[photo])
    argv = (tmp_path / "argv").read_text().splitlines()
    assert argv[argv.index("-m") + 1] == "gpt-6-luna" and argv[argv.index("-i") + 1].endswith(".jpg")
    assert (tmp_path / "key").read_text() == ""


def test_a_lapsed_login_is_said_as_one_and_a_failure_as_a_failure(tmp_path):
    with pytest.raises(CloudLoginLost):
        backend(tmp_path, fake_codex(tmp_path, [], code=1, stderr="Not logged in. Run codex login")).complete("s", "p")
    other = tmp_path / "other"
    failed = [{"type": "turn.failed", "error": {"message": "You have hit your usage limit"}}]
    with pytest.raises(RuntimeError, match="usage limit"):
        backend(other, fake_codex(other, failed, code=1)).complete("s", "p")


def test_the_models_listed_are_the_ones_codex_shows_in_its_order(tmp_path):
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": [{"slug": "b", "visibility": "list", "priority": 9},
                                            {"slug": "hidden", "visibility": "hide", "priority": 1},
                                            {"slug": "a", "visibility": "list", "priority": 5}]}))
    assert CodexCliProvider(tmp_path, models_file=cache, words=ZH).models() == ["a", "b"]
    assert CodexCliProvider(tmp_path, models_file=tmp_path / "none.json", words=ZH).models() == ["default"]
