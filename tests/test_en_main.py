"""What an English Muninn's entry point says (main.py): settings that cannot work, the settings page's models, and
what `muninn check` prints."""

from __future__ import annotations

import re

from muninn.languages import language
from muninn.main import cloud_settings, main, model_problems, running_version

EN = language("en")
HAN = re.compile(r"[一-鿿]")


def test_model_settings_that_cannot_work_are_said_in_english():
    [cloud] = model_problems({"routing": "cloud"}, cloud=False, local=False, words=EN)
    [privacy] = model_problems({"routing": "privacy"}, cloud=True, local=False, words=EN)
    assert cloud.startswith("llm.routing is cloud, but no cloud model is set up") and "location: local" in privacy
    assert HAN.search(cloud + privacy) is None


def test_the_settings_page_names_the_cloud_and_each_kind_of_work_in_english():
    llm = {"routing": "cloud", "providers": {"anthropic": {"impl": "anthropic", "key": {"env": "INVENTED_API_KEY"}},
                                             "relay": {"impl": "openai", "base_url": "https://relay.example/v1",
                                                       "key": {"env": "INVENTED_RELAY_KEY"}, "models": {"fast": "small"}}},
           "use": {"fast": "relay", "standard": "anthropic"}}
    shown = cloud_settings(llm, EN)
    assert shown["cloud_label"] == "Anthropic API, OpenAI-compatible"
    rows = {r["key"]: r for r in shown["models"]}
    assert (rows["route"]["label"], rows["mail"]["detail"]) == ("Routing", "Sorting new mail and drafting replies")
    assert all(HAN.search(r["label"] + r["detail"]) is None for r in shown["models"])


def test_an_install_without_git_says_so_in_english(tmp_path):
    assert running_version(tmp_path, tmp_path, EN).unknown == "This install has no git, so it can't tell which version is running"


def test_check_prints_what_cannot_work_in_english(tmp_path, capsys):
    (tmp_path / "config.yaml").write_text("language: en\nllm: {routing: cloud}\nallowed_tailscale_logins: [someone@example.com]\n"
                                          "host: 0.0.0.0\n")
    assert main(["--data", str(tmp_path), "check"]) == 1
    out = capsys.readouterr().out
    mine = [line for line in out.splitlines() if "llm.routing is cloud" in line or "host is 0.0.0.0" in line]
    assert len(mine) == 2 and all(line.startswith("  !!  ") and HAN.search(line) is None for line in mine)
