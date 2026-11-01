from __future__ import annotations

import json

import anthropic
import httpx2
import pytest

from muninn.adapters.llm.anthropic_api import AnthropicApiProvider
from muninn.adapters.llm.claude_cli import ClaudeCliProvider
from muninn.adapters.llm.keys import EnvKey, SecretFile
from muninn.core.llm_router import CloudFirst, LLMRequest, LLMRouter
from muninn.core.models import Attachment, Sensitivity
from muninn.core.ports import CloudLoginLost
from muninn.core.task_models import ModelChoice
from muninn.llm_config import llm_setup
from muninn.main import IMPLS, cloud_settings

from conftest import ZH

KEY = "sk-invented-test-key"


def api(handler, key_source):
    """The provider over the real SDK, its requests answered by `handler` instead of the network."""
    seen = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return handler(request)

    client_for = lambda k: anthropic.Anthropic(api_key=k, base_url="https://api.invalid", max_retries=0,  # noqa: E731
                                                http_client=httpx2.Client(transport=httpx2.MockTransport(respond)))
    return AnthropicApiProvider("anthropic", key_source, client_for=client_for, words=ZH), seen


def message(text: str, stop: str = "end_turn") -> httpx2.Response:
    return httpx2.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                                      "content": [{"type": "text", "text": text}], "stop_reason": stop,
                                      "usage": {"input_tokens": 3, "output_tokens": 2}})


def secret(tmp_path, value: str = KEY) -> SecretFile:
    (tmp_path / "anthropic-api-key").write_text(value + "\n")
    return SecretFile(tmp_path / "anthropic-api-key")


def test_a_request_goes_with_the_key_from_its_file_and_comes_back_as_the_reply(tmp_path):
    provider, seen = api(lambda r: message('{"ok": true}'), secret(tmp_path))
    b = provider.backend("claude-haiku-5-5", 30)
    assert b.name == "anthropic:claude-haiku-5-5" and b.capabilities.images and b.capabilities.pdf and not b.is_local
    assert b.complete("be brief", "hello", json_mode=True) == '{"ok": true}'
    sent = json.loads(seen[0].content)
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"  # the key given, nothing the SDK found by itself
    assert (sent["model"], sent["system"], sent["messages"]) == ("claude-haiku-5-5", "be brief", [{"role": "user", "content": "hello"}])
    assert sent["max_tokens"] == 16000


def test_photos_and_pdfs_go_as_content_blocks(tmp_path):
    provider, seen = api(lambda r: message("a receipt"), secret(tmp_path))
    files = (Attachment("r.png", "image/png", b"\x89PNG"), Attachment("a.pdf", "application/pdf", b"%PDF"))
    provider.backend("claude-sonnet-5-5").complete("s", "what is this", attachments=files)
    blocks = json.loads(seen[0].content)["messages"][0]["content"]
    assert [b["type"] for b in blocks] == ["image", "document", "text"] and blocks[-1]["text"] == "what is this"


def test_a_missing_or_rejected_key_is_a_lapsed_login_and_nothing_stands_in(tmp_path):
    provider, seen = api(lambda r: message("never"), SecretFile(tmp_path / "nowhere"))
    with pytest.raises(CloudLoginLost):
        provider.backend("claude-haiku-5-5").complete("s", "p")
    assert seen == []  # no key: no request at all
    rejected, _ = api(lambda r: httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error",
                                                                                      "message": "invalid x-api-key"}}),
                      secret(tmp_path))
    backend = rejected.backend("claude-haiku-5-5")
    assert str(tmp_path / "anthropic-api-key") in backend.login_hint
    lost = []

    class Local:
        name, is_local, label, login_hint = "ollama:x", True, "本機模型", ""
        available = staticmethod(lambda: True)
        complete = staticmethod(lambda *a, **k: "local")

    router = LLMRouter([backend, Local()], [], CloudFirst(), on_login_lost=lambda b, why: lost.append(b.name))
    c = router.complete_case(LLMRequest("s", "p", Sensitivity.SENSITIVE, None, task="mail_triage"))
    assert (c.reply, c.login_lost, lost) == (None, "Anthropic API", ["anthropic:claude-haiku-5-5"])


def test_a_reply_cut_off_or_declined_is_a_failure_not_an_answer(tmp_path):
    for stop in ("max_tokens", "refusal"):
        provider, _ = api(lambda r, stop=stop: message('{"half": ', stop), secret(tmp_path))
        with pytest.raises(RuntimeError):
            provider.backend("claude-haiku-5-5").complete("s", "p")


def test_the_deploy_check_asks_the_api_about_the_model_without_a_model_call(tmp_path):
    provider, seen = api(lambda r: httpx2.Response(200, json={"id": "claude-haiku-5-5", "type": "model", "display_name": "Claude Haiku 5.5",
                                                              "created_at": "2026-01-01T00:00:00Z"}), secret(tmp_path))
    assert provider.backend("claude-haiku-5-5").check() == "Claude Haiku 5.5"
    assert seen[0].method == "GET" and seen[0].url.path.endswith("/v1/models/claude-haiku-5-5")


def test_a_key_in_the_environment_is_read_from_it_on_every_call():
    env = {}
    key = EnvKey("INVENTED_API_KEY", env, words=ZH)
    with pytest.raises(CloudLoginLost):
        key.read()
    env["INVENTED_API_KEY"] = KEY
    assert key.read() == KEY


def test_providers_in_agent_hubs_shape_choose_a_provider_and_model_per_kind_of_work():
    setup = llm_setup({"providers": {
        "anthropic": {"impl": "anthropic", "key": {"secret": "anthropic-api-key"}, "models": {"strong": {"name": "claude-opus-5-5", "context": 1000000}}},
        "claude": {"impl": "claude"}},
        "use": {"fast": "claude", "standard": "anthropic", "strong": "anthropic"},
        "models": {"draft": "strong", "mail": "sonnet", "reflect": {"provider": "anthropic", "model": "claude-haiku-5-5"}}}, IMPLS)
    m = setup.task_models()
    assert m.model("route") == ModelChoice("anthropic", "claude-sonnet-5-5")  # the user's words: standard
    assert m.model("packet") == ModelChoice("anthropic", "claude-opus-5-5")  # moved to a tier
    assert m.model("mail_triage") == ModelChoice("claude", "sonnet")  # another model of the tier's provider
    assert m.model("reflect_week") == ModelChoice("anthropic", "claude-haiku-5-5")  # a provider and model
    assert setup.choice("fast") == ModelChoice("claude", "haiku")


def test_one_provider_serves_every_tier_without_use_and_the_settings_name_it():
    llm = {"routing": "cloud", "providers": {"anthropic": {"impl": "anthropic", "key": {"env": "INVENTED_API_KEY"}}}}
    shown = cloud_settings(llm, ZH)
    assert shown["cloud_label"] == "Anthropic API" and shown["routing"] == "cloud"
    assert {r["key"]: r["model"] for r in shown["models"]}["route"] == "claude-sonnet-5-5"


@pytest.mark.parametrize("providers, use, says", [
    ({"a": {"impl": "anthropic", "key": KEY}}, None, "write where the key is"),
    ({"a": {"impl": "anthropic", "key": {"secret": "../elsewhere"}}}, None, "write where the key is"),
    ({"a": {"impl": "openai-ish"}}, None, "known:"),
    ({"a": {"impl": "openai", "location": "local", "base_url": "https://api.example.com/v1"}}, None, "not one"),
    ({"a": {"impl": "ollama", "models": {"fast": "qwen3:8b"}}, "b": {"impl": "ollama", "models": {"fast": "x"}}}, None, "one local"),
    ({"l": {"impl": "ollama", "models": {"fast": "qwen3:8b"}}, "c": {"impl": "claude"}}, {"fast": "l"}, "use names cloud"),
    ({"a": {"impl": "anthropic", "location": "moon"}}, None, "location"),
    ({"a": {"impl": "anthropic", "models": {"huge": "m"}}}, None, "unknown tier"),
    ({"a": {"impl": "claude"}, "b": {"impl": "claude"}}, {"fast": "c"}, "provider 'c'"),
])
def test_a_config_it_cannot_use_as_written_fails_at_start_without_repeating_a_key(providers, use, says):
    with pytest.raises(ValueError, match=says) as e:
        llm_setup({"providers": providers, **({"use": use} if use else {})}, IMPLS)
    assert KEY not in str(e.value)


def test_an_api_provider_without_a_key_fails_at_start(tmp_path):
    from muninn.llm_config import ProviderContext

    setup = llm_setup({"providers": {"a": {"impl": "anthropic"}}}, IMPLS)
    with pytest.raises(ValueError, match="needs one"):
        AnthropicApiProvider.from_spec(setup.providers["a"], ProviderContext(tmp_path, tmp_path, words=ZH))


def test_the_cli_is_not_handed_another_providers_key(tmp_path, monkeypatch):
    from muninn.llm_config import ProviderContext

    monkeypatch.setenv("INVENTED_API_KEY", KEY)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "invented-oauth")
    setup = llm_setup({"providers": {"claude": {"impl": "claude"}, "anthropic": {"impl": "anthropic", "key": {"env": "INVENTED_API_KEY"}}},
                       "use": {"fast": "claude", "standard": "claude"}}, IMPLS)
    ctx = ProviderContext(tmp_path, tmp_path, frozenset(setup.env_keys()), words=ZH)
    env = ClaudeCliProvider.from_spec(setup.providers["claude"], ctx).backend("haiku")._env("/bin/claude")
    assert "INVENTED_API_KEY" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_an_older_config_keeps_every_kind_of_work_on_the_cli_models_it_had(tmp_path):
    """The shape this machine's config has had: no providers, the CLI with a token file."""
    setup = llm_setup({"routing": "cloud", "cloud": "claude-cli", "claude_binary": "~/bin/claude",
                       "claude_token_file": "~/invented-token", "cloud_model": None, "understanding_model": None}, IMPLS)
    m = setup.task_models()
    assert {t: m.model(t) for t in ("route", "facts", "answer")} == dict.fromkeys(("route", "facts", "answer"), ModelChoice("claude", "sonnet"))
    assert {t: m.model(t) for t in ("packet", "mail_triage", "reflect_week")} == dict.fromkeys(
        ("packet", "mail_triage", "reflect_week"), ModelChoice("claude", "haiku"))
    assert setup.choice("fast") == ModelChoice("claude", "haiku") and setup.choice("standard") == ModelChoice("claude", "sonnet")
    assert setup.providers["claude"].options == {"binary": "~/bin/claude", "token_file": "~/invented-token"}
    assert cloud_settings({"cloud": "claude-cli", "routing": "cloud"}, ZH)["cloud_label"] == "Claude"


def built(tmp_path, llm_yaml: str):
    from muninn.config import Config
    from muninn.main import build

    (tmp_path / "config.yaml").write_text(llm_yaml)
    (tmp_path / "attribution.yaml").write_text("sensitive_domains: [work]\n")
    (tmp_path / "secrets").mkdir()
    for name in ("google_client.json", "microsoft_client.json"):
        (tmp_path / "secrets" / name).write_text(json.dumps({"web": {"client_id": "a", "client_secret": "b"}, "client_id": "a"}))
    return build(Config.load(tmp_path), dry_run=True)


def test_a_config_with_only_an_api_key_answers_every_kind_of_work_through_it(tmp_path, monkeypatch):
    from muninn.adapters.llm.anthropic_api import AnthropicApiBackend

    monkeypatch.setattr(AnthropicApiBackend, "complete", lambda self, system, prompt, json_mode=False: self.name)
    app = built(tmp_path, "llm:\n  routing: cloud\n  providers:\n    anthropic: {impl: anthropic, key: {secret: anthropic-api-key}}\n")
    e = app.engine
    ask = lambda router, task: router.complete(LLMRequest("s", "c", Sensitivity.SENSITIVE, None, task=task))  # noqa: E731
    assert ask(e.router, "mail_triage") == "anthropic:claude-haiku-5-5"
    assert ask(e.understanding.router, "route") == "anthropic:claude-sonnet-5-5"
    assert {name for name, _ in app.smoke if name.startswith("cloud model")} == {
        "cloud model (anthropic:claude-haiku-5-5)", "cloud model (anthropic:claude-sonnet-5-5)"}


def test_with_two_providers_what_each_wrote_is_told_by_its_own_name(tmp_path):
    app = built(tmp_path, "llm:\n  routing: cloud\n  providers:\n    anthropic: {impl: anthropic, key: {env: INVENTED_API_KEY}}\n"
                          "    claude: {impl: claude, binary: /bin/echo}\n  use: {fast: claude, standard: anthropic}\n")
    assert app.engine.router.label_of("anthropic:claude-sonnet-5-5") == "Anthropic API"
    assert app.engine.router.label_of("claude-cli:haiku") == "Claude"
    from muninn.main import cloud_settings
    assert cloud_settings(app.config["llm"], ZH)["cloud_label"] == "Anthropic API、Claude"


def test_with_no_cloud_only_the_local_model_is_asked(tmp_path):
    app = built(tmp_path, "llm:\n  cloud: none\n")
    assert not [name for name, _ in app.smoke if name.startswith("cloud model")]


def test_one_decision_point_can_have_its_own_model_over_its_groups(tmp_path):
    from muninn.main import decision_points

    llm = {"providers": {"anthropic": {"impl": "anthropic", "key": {"secret": "anthropic-api-key"}}, "claude": {"impl": "claude"}},
           "use": {"fast": "claude", "standard": "anthropic", "strong": "claude"},
           "points": {"why": "strong", "work_check": {"provider": "anthropic", "model": "claude-haiku-5-5"}}}
    m = llm_setup(llm, IMPLS, decision_points()).task_models()
    assert m.model("why") == ModelChoice("claude", "opus")  # over the answer group's standard
    assert m.model("answer") == ModelChoice("anthropic", "claude-sonnet-5-5")  # the rest of its group as before
    assert m.model("work_check") == ModelChoice("anthropic", "claude-haiku-5-5")  # a point in no group
    with pytest.raises(ValueError, match="llm.points: unknown"):
        llm_setup({**llm, "points": {"rout": "fast"}}, IMPLS, decision_points())
    app = built(tmp_path, json.dumps({"llm": llm}))  # the key is read only when a request goes
    assert "cloud model (anthropic:claude-haiku-5-5)" in [name for name, _ in app.smoke]  # checked at deploy like the rest


def test_the_fast_tier_thinks_least_unless_config_says_otherwise(tmp_path):
    from muninn.adapters.llm.anthropic_api import effort_by_model
    from muninn.llm_config import ProviderContext

    models = {"fast": "claude-haiku-5-5", "standard": "claude-sonnet-5-5", "strong": "claude-opus-5-5"}
    assert effort_by_model("a", models, None) == {"claude-haiku-5-5": "low"}
    assert effort_by_model("a", models, {"fast": "medium", "standard": "low"}) == {"claude-haiku-5-5": "medium", "claude-sonnet-5-5": "low"}
    assert effort_by_model("a", {**models, "standard": "claude-haiku-5-5"}, None) == {}  # shared with standard: left as it is
    for bad, says in (({"fast": "tiny"}, "one of low"), ({"quick": "low"}, "unknown tier"), ("low", "mapping"),
                      ({"fast": "low", "standard": "high"}, "two tiers")):
        with pytest.raises(ValueError, match=says):
            effort_by_model("a", {**models, "standard": "claude-haiku-5-5"} if says == "two tiers" else models, bad)

    setup = llm_setup({"providers": {"anthropic": {"impl": "anthropic", "key": {"secret": "k"}}}}, IMPLS)
    seen = []
    client_for = lambda k: anthropic.Anthropic(api_key=k, base_url="https://api.invalid", max_retries=0,  # noqa: E731
                                                http_client=httpx2.Client(transport=httpx2.MockTransport(
                                                    lambda r: (seen.append(json.loads(r.content)), message("ok"))[1])))
    (tmp_path / "k").write_text(KEY)
    provider = AnthropicApiProvider.from_spec(setup.providers["anthropic"], ProviderContext(tmp_path, tmp_path, words=ZH))
    provider._client_for = client_for
    provider.backend("claude-haiku-5-5").complete("s", "p")
    provider.backend("claude-sonnet-5-5").complete("s", "p")
    assert seen[0]["output_config"] == {"effort": "low"} and "output_config" not in seen[1]


def test_what_a_request_took_on_the_api_is_kept_with_its_case(tmp_path):
    from muninn.adapters.store_sqlite import SqliteStore
    from muninn.core.evals import CaseRecorder
    from conftest import FakeClock, at

    provider, _ = api(lambda r: message("ok"), secret(tmp_path))
    store = SqliteStore(tmp_path / "m.db")
    b = provider.backend("claude-haiku-5-5")
    router = LLMRouter([b], [], CloudFirst(), CaseRecorder(store, FakeClock(at(2026, 10, 30, 9))), cloud_for=lambda task: [b])
    assert router.complete(LLMRequest("s", "c", Sensitivity.SENSITIVE, None, task="facts")) == "ok"
    assert store.eval_cases("facts")[0]["tokens"] == (3, 2)  # the usage the API reported


def test_a_local_provider_answers_what_may_not_leave_and_is_checked_at_deploy(tmp_path):
    app = built(tmp_path, json.dumps({"llm": {"providers": {
        "lan": {"impl": "openai", "location": "local", "base_url": "http://192.168.1.20:1234/v1", "models": {"fast": "qwen3-8b"}},
        "anthropic": {"impl": "anthropic", "key": {"secret": "k"}}}}}))
    assert app.problems == []
    assert app.engine.router.is_local("lan:qwen3-8b") and not app.engine.router.is_local("anthropic:claude-haiku-5-5")
    assert "local model (lan:qwen3-8b)" in [name for name, _ in app.smoke]


def test_with_only_a_local_model_everything_stays_here(tmp_path):
    app = built(tmp_path, "language: zh-TW\nllm:\n  providers:\n    home: {impl: ollama, models: {fast: qwen3:8b}}\n")
    assert app.problems == [] and [b.name for b in app.engine.router.backends()] == ["home:qwen3:8b"]
    assert app.engine.router.label_of("home:qwen3:8b") == "本機模型"
