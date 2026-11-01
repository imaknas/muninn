"""The settings an installation runs with, as `muninn config --effective` prints them."""

from pathlib import Path

from conftest import ZH
from muninn.config import Config
from muninn.main import main
from ruamel.yaml import YAML


def effective(capsys, data) -> dict:
    assert main(["--data", str(data), "config", "--effective"]) == 0
    return YAML(typ="safe").load(capsys.readouterr().out)


def test_the_effective_settings_are_the_file_over_the_defaults_with_what_follows_from_them(tmp_path, capsys):
    (tmp_path / "config.yaml").write_text("owner_label: Robin\nllm: {routing: cloud}\nsecrets_dir: keys\n", encoding="utf-8")
    got = effective(capsys, tmp_path)
    assert got["owner_label"] == "Robin" and got["principal_id"] == "person:robin"
    assert got["llm"]["routing"] == "cloud" and got["llm"]["ollama_url"]  # merged, not replaced
    assert got["secrets_dir"] == str(tmp_path.resolve() / "keys") and got["data_dir"] == str(tmp_path.resolve())
    assert got["timezone"] == Config.load(tmp_path).tz


def test_printing_the_settings_needs_no_keys_and_leaves_nothing_behind(tmp_path, capsys):
    before = sorted(p.name for p in tmp_path.iterdir())
    assert effective(capsys, tmp_path)["port"]
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # no database, no secrets made


def test_a_fresh_install_names_no_one_and_reads_nothing_of_its_own(tmp_path):
    cfg = Config.load(tmp_path)
    assert cfg["owner_label"] == "owner" and cfg["principal_id"] is None
    assert cfg["mail_accounts"] == [] and cfg["workspace_roots"] == [] and cfg["transcript_roots"] == []
    assert cfg["projects"]["side"] == "local"


def test_the_time_zone_unset_is_the_machines(tmp_path):
    from muninn.config import system_timezone

    link = tmp_path / "localtime"
    link.symlink_to("/usr/share/zoneinfo/Europe/Oslo")
    nowhere = tmp_path / "none"
    assert system_timezone({"TZ": ":America/Lima"}, link, nowhere) == "America/Lima"
    assert system_timezone({}, link, nowhere) == "Europe/Oslo"
    (tmp_path / "timezone").write_text("Africa/Accra\n")
    assert system_timezone({}, nowhere, tmp_path / "timezone") == "Africa/Accra"
    assert system_timezone({"TZ": "Not/AZone"}, nowhere, nowhere) == "UTC"
    (tmp_path / "config.yaml").write_text("timezone: Asia/Tokyo\n")
    assert Config.load(tmp_path).tz == "Asia/Tokyo"


def test_with_no_model_set_up_muninn_keeps_what_is_said_and_says_why_it_did_not_understand(tmp_path):
    from muninn.main import build

    (tmp_path / "config.yaml").write_text("language: zh-TW\n")  # not the machine's own language: the words below are Chinese
    app = build(Config.load(tmp_path), dry_run=True)
    assert app.problems == []  # no model at all is a choice, not a fault
    r = app.engine.commands().run("下週三前要把報告交出去")
    assert "還沒接上任何模型" in r.reply and not r.understood
    assert app.engine.store.eval_cases("route", 10) == []  # nothing was asked, so nothing is kept as a case


def test_model_settings_that_cannot_work_are_said_and_stop_the_commands_that_ask_models(tmp_path, capsys):
    from muninn.main import build, main, model_problems, settings_view

    assert model_problems({"routing": "cloud"}, cloud=False, local=False, words=ZH)[0].startswith("llm.routing 是 cloud")
    assert "location: local" in model_problems({"routing": "privacy"}, cloud=True, local=False, words=ZH)[0]
    assert model_problems({"routing": "privacy"}, cloud=True, local=True, words=ZH) == []
    assert model_problems({"routing": "cloud"}, cloud=True, local=False, words=ZH) == []
    (tmp_path / "config.yaml").write_text("language: zh-TW\nllm: {routing: cloud}\n")
    app = build(Config.load(tmp_path), dry_run=True)
    assert app.problems and app.engine is not None  # built all the same: the settings page says what to fix
    shown = settings_view(app.config, app.engine.composer.config, app.problems)
    assert shown["problems"] == app.problems and shown["routing"] == "cloud"  # as written, not quietly privacy
    assert main(["--data", str(tmp_path), "--dry-run", "tick"]) == 2
    assert main(["--data", str(tmp_path), "check"]) == 1
    assert "llm.routing 是 cloud" in capsys.readouterr().out


def test_what_an_installation_does_without_is_said_with_what_turns_it_on(tmp_path, capsys):
    from muninn.main import Present, build, features_off, main, settings_view

    nothing = Present(runner=False, google=False, microsoft=False, mail_accounts=(), mailboxes=0, reflect=False,
                      reflect_model=False, form_filling=False, project_folders=False, transcripts=False)
    off = {o["feature"]: o["why"] for o in features_off(nothing, words=ZH)}
    assert list(off) == ["跟專案深談(沙盒、權限卡、問專案、把想法升級成 repo)", "行事曆", "信件", "每週回顧", "代填應徵表單",
                         "專案資料夾裡的進度", "coding agent session 裡的進度"]
    assert "lease_runner_url" in off["跟專案深談(沙盒、權限卡、問專案、把想法升級成 repo)"] and "reflect: false" in off["每週回顧"]
    unread = features_off(Present(runner=True, google=True, microsoft=False, mail_accounts=("outlook",), mailboxes=0, reflect=True,
                                  reflect_model=False, form_filling=True, project_folders=True, transcripts=True), words=ZH)
    assert [(o["feature"], o["why"]) for o in unread] == [
        ("信件", "mail_accounts 有列信箱,但它們的 client 沒設定(secrets/microsoft_client.json,還有 public_url)"),
        ("每週回顧", "隱私模式下只有本機模型能讀對話,但沒有設定本機模型")]
    everything = Present(runner=True, google=True, microsoft=True, mail_accounts=("gmail",), mailboxes=1, reflect=True,
                         reflect_model=True, form_filling=True, project_folders=True, transcripts=True)
    assert features_off(everything, words=ZH) == []
    nothing_understood = features_off(Present(runner=True, google=True, microsoft=True, mail_accounts=(), mailboxes=0, reflect=True,
                                              reflect_model=True, form_filling=True, project_folders=True, transcripts=True,
                                              model=False), words=ZH)
    assert nothing_understood[0]["feature"].startswith("聽懂你說的話") and "llm.providers" in nothing_understood[0]["why"]

    (tmp_path / "config.yaml").write_text("language: zh-TW\nreflect: false\n")
    app = build(Config.load(tmp_path), dry_run=True)
    features = [o["feature"] for o in app.off]
    assert "每週回顧" in features and "行事曆" in features and "跟專案深談(沙盒、權限卡、問專案、把想法升級成 repo)" in features
    assert settings_view(app.config, app.engine.composer.config, app.problems, app.off)["off"] == app.off
    capsys.readouterr()
    main(["--data", str(tmp_path), "check"])
    out = capsys.readouterr().out
    assert "  --  每週回顧: 設定關掉了(reflect: false)" in out  # said, as something done without, not a failure (!!)
    assert not any(line.startswith("  !!") and "每週回顧" in line for line in out.splitlines())


def test_a_backup_is_the_database_plans_and_settings_and_restores_by_copying_back(tmp_path, capsys):
    from muninn.adapters.store_sqlite import SqliteStore
    from muninn.main import main

    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text("owner_label: Robin\n")
    (data / "domains").mkdir()
    (data / "domains" / "garden.yaml").write_text("domain: {id: garden, title: 院子}\n")
    (data / "secrets").mkdir()
    (data / "secrets" / "ingest_token").write_text("invented-token")
    SqliteStore(data / "muninn.db").set_state("k", {"v": 1})
    assert main(["--data", str(data), "backup", str(tmp_path / "copies")]) == 0
    copy = Path(capsys.readouterr().out.strip())
    assert sorted(p.name for p in copy.iterdir()) == ["config.yaml", "domains", "muninn.db"]  # no keys unless asked
    assert SqliteStore(copy / "muninn.db").get_state("k") == {"v": 1}  # the database whole, readable as it was
    assert oct(copy.stat().st_mode)[-3:] == "700" and oct((copy / "config.yaml").stat().st_mode)[-3:] == "600"
    assert main(["--data", str(data), "backup", str(tmp_path / "copies"), "--secrets"]) == 0
    assert (Path(capsys.readouterr().out.strip()) / "secrets" / "ingest_token").read_text() == "invented-token"


def test_check_warns_when_the_login_allowlist_can_be_walked_around(tmp_path):
    from muninn.main import access_problems

    (tmp_path / "config.yaml").write_text("allowed_tailscale_logins: [you@example.com]\nhost: 0.0.0.0\n")
    assert "tailscale serve" in access_problems(Config.load(tmp_path))[0]
    (tmp_path / "config.yaml").write_text("allowed_tailscale_logins: [you@example.com]\n")
    assert access_problems(Config.load(tmp_path)) == []


def test_a_container_says_where_to_listen_in_its_environment(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("host: 127.0.0.1\n")
    monkeypatch.setenv("MUNINN_HOST", "0.0.0.0")
    assert Config.load(tmp_path)["host"] == "0.0.0.0"
