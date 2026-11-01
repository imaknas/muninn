"""What Muninn asks a model is written in English whatever language it speaks, and names that language for whatever
a person reads of the answer."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from conftest import at, make_harness
from muninn.core import answer
from muninn.core.tools.builtin import BUILTIN
from muninn.languages import language

ROOT = Path(__file__).resolve().parents[1]
HAN = re.compile(r"[一-鿿]")
# the tasks whose answer has words a person reads (a reply, a summary, a why, a draft, a reflection)
READ = {"route", "facts", "mail_triage", "task_evidence", "packet", "packet_fit", "form_fill", "evening", "reflect_digest", "reflect_week"}


def snapshot():
    spec = importlib.util.spec_from_file_location("prompt_snapshot", ROOT / "bin" / "prompt_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prompts


def every_prompt(code: str) -> dict[str, str]:
    words = language(code)
    return {**snapshot()(words), "answer": answer.system(words)}


@pytest.mark.parametrize("code", ["en", "zh-TW"])
def test_every_prompt_is_in_english_and_fills_in_the_language(code):
    for task, prompt in every_prompt(code).items():
        assert "{language}" not in prompt, task
        if code == "en":
            assert not HAN.search(prompt), (task, HAN.findall(prompt)[:10])


def test_a_prompt_asking_for_words_a_person_reads_names_the_language_they_read():
    en, zh = every_prompt("en"), every_prompt("zh-TW")
    for task in READ | {"answer"}:
        assert language("en").name in en[task] and "Chinese" not in en[task], task
        assert language("zh-TW").name in zh[task] and "Traditional Chinese" in zh[task], task
    for task in set(en) - READ - {"answer"}:  # nothing in it for a person to read: the same prompt in either language
        assert en[task] == zh[task], task


def test_the_routing_prompt_the_snapshot_takes_is_the_golden_one():
    golden = (ROOT / "tests" / "golden" / "command_prompt.txt").read_text()
    assert every_prompt("zh-TW")["route"] == golden


def test_the_tools_and_the_notes_beside_what_is_discussed_are_in_english():
    assert [c.name for c in BUILTIN if HAN.search(c.description)] == []
    h = make_harness(at(2026, 10, 22, 10), lang="en")
    n = h.engine.ideas.add("A shelf planner", "shelf-planner")["n"]
    notes = [h.engine.about("idea", str(n))["note"], h.engine.about("onboarding", "first")["note"]]
    assert [x for x in notes if HAN.search(x)] == []
    assert '"Plan review"' in notes[1] and '"Settings"' in notes[1]  # the screens, named as the page names them
    zh = make_harness(at(2026, 10, 22, 10)).engine.about("onboarding", "first")["note"]
    assert "「計畫確認」" not in zh and '"計畫確認"' in zh and '"設定"' in zh
