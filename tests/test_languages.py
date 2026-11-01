"""Muninn speaks Traditional Chinese or English (core/language.py, muninn/languages/): every language says
everything the code asks for, with the same values, and the page and config agree on which one it is."""

from __future__ import annotations

import inspect
import re
import string
from pathlib import Path

from starlette.testclient import TestClient

from conftest import at, make_harness
from muninn.adapters.channels.webpush import VapidKeys
from muninn.adapters.web.app import create_app
from muninn.adapters.web.devices import DeviceRegistry
from muninn.config import Config
from muninn.languages import CODES, language, system_language

SRC = Path(__file__).resolve().parents[1] / "src" / "muninn"


def params(text) -> set[str]:
    if callable(text):
        return set(inspect.signature(text).parameters)
    return {name.split(".")[0].split("[")[0] for _, name, _, _ in string.Formatter().parse(text) if name}


def test_every_language_has_every_text_with_the_same_values():
    zh, en = language("zh-TW"), language("en")
    assert set(zh.texts) == set(en.texts)
    assert {k: params(v) for k, v in zh.texts.items()} == {k: params(v) for k, v in en.texts.items()}


def test_every_text_the_code_asks_for_is_there():
    used = {m for f in SRC.rglob("*.py") if "languages" not in f.parts
            for m in re.findall(r"\bwords\(\s*[\"']([a-z_]+\.[a-z0-9_]+)[\"']", f.read_text(encoding="utf-8"))}
    assert len(used) >= 10 and used - set(language("en").texts) == set()


def test_a_language_joins_and_cuts_sentences_its_own_way():
    zh, en = language("zh-TW"), language("en")
    assert zh.join(["a", "b"]) == "a、b" and en.join(["a", "b"]) == "a, b"
    assert zh.sentences("好。明天見!再說") == ["好。", "明天見!", "再說"]
    text = "Done. Ask /help now?\nFine"
    assert en.sentences(text) == ["Done. ", "Ask /help now?\n", "Fine"] and "".join(en.sentences(text)) == text


def test_unset_the_language_is_the_machines_own_and_one_muninn_does_not_speak_is_refused(tmp_path):
    assert system_language({"LANG": "zh_TW.UTF-8"}) == "zh-TW" and system_language({"LC_ALL": "en_GB.UTF-8", "LANG": "zh_TW"}) == "en"
    assert system_language({}) == "en"
    (tmp_path / "config.yaml").write_text("language: en\n")
    assert Config.load(tmp_path).language == "en" and Config.load(tmp_path).effective()["language"] == "en"
    try:
        language("fr")
    except ValueError as e:
        assert ", ".join(CODES) in str(e)
    else:
        raise AssertionError("fr is not a language Muninn speaks")


def test_the_page_says_which_language_it_is_in(tmp_path):
    for code in CODES:
        h = make_harness(at(2026, 11, 3, 9), lang=code)
        keys = VapidKeys(tmp_path / f"{code}.pem")
        keys.ensure()
        client = TestClient(create_app(h.engine, keys, "tok", DeviceRegistry(tmp_path / f"{code}.db")), base_url="https://testserver")
        assert f'<html lang="{code}">' in client.get("/").text


def test_the_privacy_page_is_in_the_language_muninn_speaks(tmp_path):
    han = re.compile(r"[\u3400-\u9fff]")
    for code in CODES:
        h = make_harness(at(2026, 11, 3, 9), lang=code)
        keys = VapidKeys(tmp_path / f"{code}.pem")
        keys.ensure()
        client = TestClient(create_app(h.engine, keys, "tok", DeviceRegistry(tmp_path / f"{code}.db")), base_url="https://testserver")
        page = client.get("/privacy")
        assert page.status_code == 200
        assert bool(han.search(page.text)) == (code == "zh-TW"), code

def test_a_language_muninn_does_not_speak_in_config_is_said_in_one_line_and_nothing_starts(tmp_path, capsys):
    from muninn.main import main

    (tmp_path / "config.yaml").write_text("language: fr\n")
    assert main(["--data", str(tmp_path), "config", "--effective"]) == 2
    assert capsys.readouterr().err.strip() == "language: one of zh-TW, en (config says 'fr')"
    assert main(["--data", str(tmp_path), "check"]) == 2


def test_the_offline_page_keeps_every_screens_words():
    ts = SRC / "adapters" / "web" / "ts"
    areas = sorted(p.stem for p in (ts / "words" / "zh-TW").glob("*.ts"))
    assert areas == sorted(p.stem for p in (ts / "words" / "en").glob("*.ts"))
    listed = re.search(r"const WORD_AREAS = \[([^\]]*)\]", (ts / "sw.ts").read_text(encoding="utf-8"))
    assert listed and sorted(re.findall(r'"([^"]+)"', listed.group(1))) == areas
