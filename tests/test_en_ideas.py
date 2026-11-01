"""In English, the ideas say why one cannot be renamed, settled, explored or made a project in English, and what
Muninn offers next about an idea is offered in English (core/ideas.py, core/idea_next.py). Invented ideas only."""

from __future__ import annotations

import re

import pytest

from muninn.core.idea_next import IdeaNext
from muninn.core.ports import SandboxRefused
from muninn.languages import language

from conftest import at, make_harness

EN = language("en")


def english(text: str) -> bool:
    return re.search(r"[一-鿿]", text) is None


def test_what_cannot_be_done_with_an_idea_is_said_in_english():
    h = make_harness(at(2026, 10, 24, 10), lang="en")
    ideas = h.engine.ideas
    n = ideas.add("A rain gauge from a bottle")["n"]
    with pytest.raises(ValueError, match="^Which idea\\?$"):
        ideas.settle(99, "shelved")
    with pytest.raises(ValueError, match="^Shelve it, drop it, or take it back up\\?$"):
        ideas.settle(n, "lost")
    with pytest.raises(ValueError) as bad:
        ideas.rename(n, "Rain Gauge!")
    assert str(bad.value).endswith('"Rain Gauge!" won\'t do') and english(str(bad.value))
    with pytest.raises(ValueError) as none:
        ideas.explore(n)
    assert str(none.value) == "Can't open a project to explore it yet (the projects side isn't ready)"


def test_what_the_projects_side_refused_is_said_in_english():
    h = make_harness(at(2026, 10, 24, 10), lang="en")
    n = h.engine.ideas.add("A rain gauge from a bottle")["n"]

    class Full:
        def create(self, name, purpose):
            raise SandboxRefused("too_many", limit=3, current=3)

    h.engine.sandboxes = Full()
    with pytest.raises(ValueError) as full:
        h.engine.ideas.explore(n)
    assert str(full.value) == "Projects for exploring ideas: 3 are open and the limit is 3; close the ones you don't use first"
    taken = EN("ideas.refused", kind="taken", doing="rename", limit=None, current=None, detail="")
    assert taken == "That name is already another project's, so renaming it didn't work; pick another name"
    assert EN("ideas.refused", kind="odd", doing="promote", limit=None, current=None, detail="") == "Making it a project failed (odd)"


class Decider:
    def decide(self, q):
        return None


def test_making_an_idea_a_project_is_offered_in_english():
    idea = {"kind": "idea", "ref": 4, "name": "rain-gauge", "title": "A rain gauge from a bottle", "status": "open"}
    p = {"actions": [], "reply": "It worked.", "suggest": [], "about": idea, "next": "promote"}
    IdeaNext(Decider(), lambda n: idea, words=EN).apply(p, "nice", acted=False, marks=())
    assert p["reply"].startswith("It worked.\nThis is worth making a real project. Shall I make rain-gauge a project?")
    assert p["suggest"][0]["label"] == "Make it a project" and english(p["reply"])
    q = {"actions": [], "reply": "", "suggest": [], "about": idea, "next": "offer_experiment"}
    IdeaNext(Decider(), lambda n: idea, words=EN).apply(q, "hm", acted=False, marks=())
    assert q["suggest"] == [{"label": "Try it in a sandbox", "tool": "explore_idea", "args": {"idea": 4}}]
