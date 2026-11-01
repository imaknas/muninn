"""Setting Muninn up further from the settings page: the folders it reads activity from, the Google client it signs
in with, and the browser that fills in application forms."""

from __future__ import annotations

import json
import stat
from types import SimpleNamespace

import pytest
from ruamel.yaml import YAML

from muninn.adapters import installs
from muninn.config import Config
from muninn.form_filling import FormFilling
from muninn.main import IMPLS
from muninn.setup_flow import Setup
from muninn.upgrade import PACKAGE, UpgradeError

URL = "https://muninn.example.ts.net"
CALLBACK = f"{URL}/oauth/google/callback"


def setup(tmp_path, config="", restarts=False):
    (tmp_path / "config.yaml").write_text(config, encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return Setup(Config.load(tmp_path), IMPLS, restart=lambda: restarts, home=home), home


def written(tmp_path):
    return YAML(typ="safe").load((tmp_path / "config.yaml").read_text(encoding="utf-8")) or {}


def test_the_usual_folders_found_at_home_are_offered_and_the_listed_ones_are_not(tmp_path):
    s, home = setup(tmp_path, "workspace_roots: [~/code]\n")
    for d in ("code", "workspace", ".claude/projects"):
        (home / d).mkdir(parents=True)
    got = s.sources()
    assert got["workspace_roots"] == ["~/code"]
    assert got["suggested"] == {"workspace_roots": ["~/workspace"], "transcript_roots": ["~/.claude/projects"]}


def test_folders_chosen_are_written_once_each_keeping_the_rest_of_the_config(tmp_path):
    s, _ = setup(tmp_path, "# mine\nlanguage: en\n", restarts=True)
    work = tmp_path / "work"
    work.mkdir()
    assert s.set_sources({"workspace_roots": [str(work), str(work), " "], "transcript_roots": []}) is True
    assert written(tmp_path) == {"language": "en", "workspace_roots": [str(work)], "transcript_roots": []}
    assert "# mine" in (tmp_path / "config.yaml").read_text(encoding="utf-8")


def test_a_folder_that_is_not_there_is_said_and_nothing_is_written(tmp_path):
    s, _ = setup(tmp_path, "language: en\n")
    with pytest.raises(ValueError, match="nowhere"):
        s.set_sources({"workspace_roots": [str(tmp_path / "nowhere")]})
    assert written(tmp_path) == {"language": "en"}


def client(**web):
    return json.dumps({"web": {"client_id": "x.apps.googleusercontent.com", "client_secret": "s3cret", "redirect_uris": [CALLBACK], **web}})


def test_a_google_client_is_kept_where_only_its_owner_reads_it_and_gmail_is_listed_once(tmp_path):
    s, _ = setup(tmp_path, f"language: en\npublic_url: {URL}\n", restarts=True)
    assert s.google_client(client(), gmail=True) is True
    kept = tmp_path / "secrets" / "google_client.json"
    assert json.loads(kept.read_text())["web"]["client_secret"] == "s3cret"
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600
    s.google_client(client(), gmail=True)
    assert written(tmp_path)["mail_accounts"] == [{"name": "personal", "kind": "personal", "provider": "gmail"}]


@pytest.mark.parametrize("text,said", [
    ("not json", "not the file Google"),
    (json.dumps({"installed": {"client_id": "x", "client_secret": "s"}}), "Desktop"),
    (json.dumps({"web": {"client_id": "x"}}), "no Web application client"),
    (client(redirect_uris=["https://elsewhere.example/cb"]), CALLBACK),
])
def test_a_client_that_could_not_sign_in_is_refused_with_what_to_do(tmp_path, text, said):
    s, _ = setup(tmp_path, f"language: en\npublic_url: {URL}\n")
    with pytest.raises(ValueError, match=said.replace("?", r"\?")):
        s.google_client(text, gmail=False)
    assert not (tmp_path / "secrets" / "google_client.json").exists()


def test_without_an_https_address_google_has_nowhere_to_send_you_back(tmp_path):
    s, _ = setup(tmp_path, "language: en\n")
    assert s.google_callback() is None
    with pytest.raises(ValueError, match="HTTPS"):
        s.google_client(client(), gmail=False)


def test_an_upgrade_keeps_the_extras_installed():
    assert installs.with_extras(f"{PACKAGE}==0.6.0", ("autofill",)) == f"{PACKAGE}[autofill]==0.6.0"
    assert installs.with_extras(f"{PACKAGE}==0.6.0", ()) == f"{PACKAGE}==0.6.0"
    assert installs.with_extras("https://example.com/muninn_ai-0.6.0-py3-none-any.whl", ("autofill",)) \
        == f"{PACKAGE}[autofill] @ https://example.com/muninn_ai-0.6.0-py3-none-any.whl"
    ran = []
    installs.UvTool("/usr/bin/uv", "3.13", ("autofill",), run=lambda argv, **kw: ran.append(argv) or SimpleNamespace(returncode=0)).install(f"{PACKAGE}==0.6.0")
    assert ran[0][-1] == f"{PACKAGE}[autofill]==0.6.0"


def test_adding_an_extra_installs_this_version_with_it_then_its_browser():
    ran = []
    uv = installs.UvTool("/usr/bin/uv", "3.13", executable="/tool/bin/python", run=lambda argv, **kw: ran.append(argv) or SimpleNamespace(returncode=0))
    uv.add_extra("autofill", "0.5.0", ["-m", "playwright", "install", "chromium"])
    assert ran[0][-1] == f"{PACKAGE}[autofill]==0.5.0" and "--force" in ran[0]
    assert ran[1] == ["/tool/bin/python", "-m", "playwright", "install", "chromium"]


def test_where_muninn_cannot_add_it_the_reason_is_said_before_anything_is_tried():
    c = installs.installation(env={"MUNINN_CONTAINER": "1"})
    assert "container" in (c.extra_refusal() or "") and "docker compose pull" not in c.extra_refusal()
    with pytest.raises(UpgradeError):
        c.add_extra("autofill", "0.5.0")


class Install:
    def __init__(self, fails=None):
        self.fails, self.added = fails, []

    def extra_refusal(self):
        return None

    def add_extra(self, extra, version, then=None):
        if self.fails:
            raise UpgradeError(self.fails)
        self.added.append((extra, version, then))


def test_form_filling_is_installed_apart_and_muninn_starts_again_to_use_it():
    work = []
    inst = Install()
    f = FormFilling(inst, "0.5.0", ready=lambda: False, restart=lambda: True, thread=work.append)
    assert f.start() == {"state": "installing"} and f.start() == {"state": "installing"} and len(work) == 1  # once
    assert f.state()["state"] == "installing"
    work[0]()
    assert inst.added == [("autofill", "0.5.0", ["-m", "playwright", "install", "chromium"])]
    assert f.state()["state"] == "restarting"


def test_a_failed_install_says_why_and_can_be_tried_again():
    f = FormFilling(Install(fails="no space left on device"), "0.5.0", ready=lambda: False, restart=lambda: True, thread=lambda work: work())
    f.start()
    assert f.state() == {"ready": False, "state": "failed", "said": "no space left on device", "refused": None}
    f.start()
    assert f.state()["state"] == "failed"


def test_a_service_that_does_not_answer_while_listing_models_is_said_plainly(tmp_path):
    class Down:
        def models(self):
            raise ConnectionError("Connection refused")

    class Impl:
        default_models, location, counts_tokens = {}, "cloud", True

        @staticmethod
        def label(words):
            return "Down"

        @staticmethod
        def from_spec(spec, ctx):
            return Down()

    (tmp_path / "config.yaml").write_text("language: en\n")
    s = Setup(Config.load(tmp_path), {**IMPLS, "openai": Impl}, restart=lambda: False)
    with pytest.raises(RuntimeError, match="did not answer.*Connection refused"):
        s.models({"impl": "openai", "base_url": "http://127.0.0.1:9", "key": "invented"})


def test_a_service_on_this_computer_needs_no_key_and_is_the_local_model(tmp_path):
    class Answering(Setup):
        def _answers(self, name, spec, model, words):
            pass

    (tmp_path / "config.yaml").write_text("language: en\n")
    s = Answering(Config.load(tmp_path), IMPLS, restart=lambda: True)
    assert s.save({"impl": "openai", "local": True, "base_url": "http://127.0.0.1:1234/v1", "label": "LM Studio"}, "qwen-small") is True
    llm = written(tmp_path)["llm"]
    assert llm["providers"]["lm-studio"] == {"impl": "openai", "base_url": "http://127.0.0.1:1234/v1", "label": "LM Studio",
                                              "location": "local", "models": {"fast": "qwen-small"}}
    assert llm["routing"] == "privacy" and not (tmp_path / "secrets").exists()  # no key asked, none kept


def test_an_unexpected_failure_while_installing_is_said_not_left_installing():
    class Broken(Install):
        def add_extra(self, extra, version, then=None):
            raise OSError("disk full")

    f = FormFilling(Broken(), "0.5.0", ready=lambda: False, restart=lambda: True, thread=lambda work: work())
    f.start()
    assert f.state()["state"] == "failed" and "disk full" in f.state()["said"]
