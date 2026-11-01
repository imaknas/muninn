#!/usr/bin/env python3
"""The system prompt of every model task, as this checkout builds them for one language, written as JSON
({task: prompt}) for `muninn eval --prompt-check BEFORE AFTER`: take one before changing prompts and one after.

    uv run python bin/prompt_snapshot.py [--language zh-TW|en] > before.json

The language is the one the recorded cases were asked in (by default the machine's own, as Muninn picks it when
`language` is not set). The routing prompt is the one the model reads with its tool list filled in
(tests/golden/command_prompt.txt is it in zh-TW, which a test keeps equal to what the code builds)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from muninn.core import (apply, classify, commands, engine, facts, idea_next, mail, prepare, reflect, task_evidence,  # noqa: E402
                         todo_match, topic_memory, topics, understanding, window_classify)
from muninn.core.language import Language  # noqa: E402
from muninn.core.tools.builtin import BUILTIN  # noqa: E402
from muninn.languages import CODES, language, system_language  # noqa: E402


def prompts(words: Language) -> dict[str, str]:
    """Every task's prompt as it is asked for `words`' language."""
    tools = "\n".join(f"- {c.name}: {c.description}" for c in BUILTIN)
    return {
        "route": commands.system(words, tools),
        "turn_topic": topics.SYSTEM,
        "lent_turn": topics.LENT,
        "mail_triage": mail.mail_system(words),
        "facts": facts.system(words),
        "work_check": understanding.WORK_SYSTEM,
        "domain_classify": classify.SYSTEM,
        "window_classify": window_classify.SYSTEM,
        "todo_match": todo_match.SYSTEM,
        "task_match": todo_match.TASKS_SYSTEM,
        "task_evidence": task_evidence.system(words),
        "packet": prepare.packet_system(words),
        "packet_fit": prepare.fit_system(words),
        "form_fill": apply.fill_system(words),
        "evening": engine.evening_system(words),
        "reflect_digest": reflect.digest_system(words),
        "reflect_week": reflect.weekly_system(words),
        "topic_state": topic_memory.STATE_SYSTEM,
        "topic_recall": topic_memory.RECALL_SYSTEM,
        "idea_next": idea_next.SYSTEM,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--language", choices=CODES, default=system_language())
    print(json.dumps(prompts(language(parser.parse_args().language)), ensure_ascii=False, indent=1))
