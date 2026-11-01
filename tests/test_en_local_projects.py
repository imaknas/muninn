"""What an English Muninn writes into a project folder's inbox for its agent (adapters/local_projects.py)."""

from __future__ import annotations

import re

from muninn.adapters.local_projects import LocalProjects
from muninn.languages import language


def test_a_handoff_is_written_for_the_projects_agent_in_english(tmp_path):
    garden = tmp_path / "garden"
    (garden / ".muninn").mkdir(parents=True)
    side = LocalProjects([garden], words=language("en"))
    h = {"v": 1, "id": 7, "ts": "2026-10-22T10:00:00+08:00", "status": "pending", "project": "garden", "effect": "answers",
         "item": "Red or white roses?", "fact": "Red", "quote": "the red ones", "why": "they match the fence",
         "reply_to": "garden:garden-q1", "task": "garden:t-3"}
    assert side.deliver([h]) == [7]
    inbox = (garden / ".muninn" / "inbox.md").read_text(encoding="utf-8")
    assert "- About: Red or white roses?\n- To do: Red\n- Their words: the red ones\n- Why: they match the fence\n" in inbox
    assert "- In reply to: garden-q1\n- Task already handled: t-3\n" in inbox and re.search(r"[一-鿿]", inbox) is None


def test_a_heading_the_contract_names_in_english_still_means_a_decision_first(tmp_path):
    garden = tmp_path / "garden"
    garden.mkdir()
    (garden / "TODO.md").write_text("## Pending\n- [ ] Pick a rose colour\n## 待定\n- [ ] Pick a pot\n", encoding="utf-8")
    side = LocalProjects([garden], words=language("en"))
    assert [(t.owner, t.text) for t in side.todos()] == [("pending", "Pick a rose colour"), ("pending", "Pick a pot")]
