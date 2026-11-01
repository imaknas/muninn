from __future__ import annotations

import json

import pytest

from conftest import RecordingLLM
from muninn.core.llm_router import CloudFirst, LLMRequest, LLMRouter
from muninn.core.models import Sensitivity
from muninn.core.task_models import ModelChoice, TaskModels

SONNET, HAIKU, OPUS = (ModelChoice("claude", m) for m in ("sonnet", "haiku", "opus"))
DEFAULTS = {"route": SONNET, "facts": SONNET, "answer": SONNET, "draft": HAIKU, "mail": HAIKU, "reflect": HAIKU}


def test_a_group_left_out_keeps_its_default():
    m = TaskModels(DEFAULTS, {"draft": OPUS, "reflect": None})
    assert m.model("packet") == OPUS and m.model("reflect_week") == HAIKU and m.model("why") == SONNET
    assert m.model("window_classify") is None  # in no group: the router's own model
    rows = {r["key"]: r for r in m.table()}
    assert rows["draft"]["model"] == "opus" and rows["draft"]["configured"]
    assert rows["mail"]["model"] == "haiku" and not rows["mail"]["configured"]


def test_a_misspelt_group_fails_at_start():
    with pytest.raises(ValueError, match="drafts"):
        TaskModels(DEFAULTS, {"drafts": OPUS})


def test_the_router_asks_which_cloud_model_for_each_task():
    haiku, opus, local = RecordingLLM("claude-cli:haiku", False), RecordingLLM("claude-cli:opus", False), RecordingLLM("ollama", True)
    by_model = {HAIKU: haiku, OPUS: opus}
    m = TaskModels(DEFAULTS, {"draft": OPUS})
    router = LLMRouter([haiku, local], [], CloudFirst(), cloud_for=lambda task: [by_model[m.model(task) or HAIKU]])
    ask = lambda task: router.complete_with_backend(LLMRequest("s", "c", Sensitivity.SENSITIVE, None, task=task))[1]  # noqa: E731
    assert ask("packet") == "claude-cli:opus" and ask("mail_triage") == "claude-cli:haiku" and ask("evening") == "claude-cli:haiku"
    opus.up = False
    assert ask("packet") is None and local.calls == []  # that model unreachable: nothing answers, no local model


def test_config_names_the_model_per_task_and_settings_show_it(tmp_path, monkeypatch):
    from muninn.adapters.llm.claude_cli import ClaudeCliBackend
    from muninn.config import Config
    from muninn.main import build, settings_view

    monkeypatch.setattr(ClaudeCliBackend, "complete", lambda self, system, prompt, json_mode=False: self.name)
    (tmp_path / "config.yaml").write_text(
        "llm:\n  cloud: claude-cli\n  routing: cloud\n  claude_binary: /bin/echo\n  models: {mail: sonnet, route: haiku}\n")
    (tmp_path / "attribution.yaml").write_text("sensitive_domains: [work]\n")
    (tmp_path / "secrets").mkdir()
    for name in ("google_client.json", "microsoft_client.json"):
        (tmp_path / "secrets" / name).write_text(json.dumps({"web": {"client_id": "a", "client_secret": "b"}, "client_id": "a"}))
    app = build(Config.load(tmp_path), dry_run=True)
    e = app.engine
    ask = lambda router, task: router.complete(LLMRequest("s", "c", Sensitivity.SENSITIVE, None, task=task))  # noqa: E731
    assert ask(e.router, "mail_triage") == "claude-cli:sonnet"  # configured
    assert ask(e.router, "packet") == "claude-cli:haiku"  # left out: cloud_model, as before
    assert ask(e.understanding.router, "route") == "claude-cli:haiku"  # configured
    assert ask(e.understanding.router, "facts") == "claude-cli:sonnet"  # left out: understanding_model, as before
    rows = {r["key"]: r for r in settings_view(app.config, e.composer.config)["models"]}
    assert (rows["mail"]["model"], rows["mail"]["configured"]) == ("sonnet", True)
    assert (rows["answer"]["model"], rows["answer"]["configured"]) == ("sonnet", False)


def test_the_cloud_is_whichever_provider_config_names_with_its_own_default_models():
    from muninn.adapters.llm.claude_cli import ClaudeCliProvider
    from muninn.main import IMPLS
    from muninn.llm_config import llm_setup

    assert llm_setup({"cloud": "none"}, IMPLS) is None
    with pytest.raises(ValueError, match="known:"):
        llm_setup({"cloud": "some-other-cloud"}, IMPLS)
    route = lambda llm: llm_setup(llm, IMPLS).task_models().model("route")  # noqa: E731
    assert route({"cloud": "claude-cli"}) == ModelChoice("claude", ClaudeCliProvider.default_models["standard"])
    assert route({"cloud": "claude-cli", "understanding_model": "m-big"}) == ModelChoice("claude", "m-big")
