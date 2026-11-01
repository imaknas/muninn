"""Tools for the user's ideas: keep one, set one aside or drop it, explore one, make one a project."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from . import Outcome, Turn

if TYPE_CHECKING:
    from ..ideas import Ideas


def idea_number(a: dict[str, Any], turn: Turn, ideas: Ideas | None = None) -> int:
    """The idea named by its number or its name, else the one being discussed, else the one this conversation
    just kept (the model sees the idea, not its number, and tends to write its words where the
    number belongs; an idea kept in a reply has no number yet when that reply offers to explore it)."""
    about, made = turn.about or {}, turn.made or {}
    for ref in (a.get("idea"), about.get("ref") if about.get("kind") == "idea" else None,
                made.get("id") if made.get("kind") == "idea" else None):
        try:
            return int(str(ref).strip().lstrip("#"))
        except (TypeError, ValueError):
            n = ideas.by_name(str(ref)) if ideas is not None and ref else None
            if n is not None:
                return n
    raise ValueError(turn.words("tools_ideas.which"))


class CaptureIdea:
    name: ClassVar[str] = "capture_idea"
    description: ClassVar[str] = ("The user tells an idea, a notion, or a new direction the user wants to see is feasible, not part of any existing plan yet (e.g. \"I want to make a…\", \"Got an idea: …\", "
                                  "\"Not sure whether … would work\"). args: {idea: <the idea, one sentence, in the user's words>, "
                                  "name: <its code name: lowercase English joined with -, two or three words, telling at a glance what it is (like shelf-planner); if the user gave one, use the user's>}")
    lookup: ClassVar[bool] = False
    acts_on: ClassVar[tuple[str, ...]] = ("idea",)

    def __init__(self, ideas: Ideas):
        self._ideas = ideas

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        got = self._ideas.capture(str(a.get("idea") or ""), str(a.get("name") or ""))
        if got is None:
            raise ValueError(turn.words("tools_ideas.what"))
        r, n = got
        return Outcome(r.text, action_id=r.id, made={"kind": "idea", "id": n})


class SettleIdea:
    name: ClassVar[str] = "settle_idea"
    description: ClassVar[str] = ("The user decided on an idea: set it aside for now, drop it, or take a shelved one back up. args: {idea: <idea number; may be left out for the one under discussion>, "
                                  "decision: <shelve | drop | reopen>, why: <one sentence, the reason the user gave; empty if the user gave none>}")
    lookup: ClassVar[bool] = False
    acts_on: ClassVar[tuple[str, ...]] = ("idea",)
    DECISIONS: ClassVar[dict[str, str]] = {"shelve": "shelved", "drop": "dropped", "reopen": "open"}

    def __init__(self, ideas: Ideas):
        self._ideas = ideas

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        decision = self.DECISIONS.get(str(a.get("decision") or ""))
        if decision is None:
            raise ValueError(turn.words("tools_ideas.which_decision"))
        r = self._ideas.settle(idea_number(a, turn, self._ideas), decision, str(a.get("why") or ""))
        if r is None:
            raise ValueError(turn.words("tools_ideas.already"))
        return Outcome(r.text, action_id=r.id)


class ExploreIdea:
    name: ClassVar[str] = "explore_idea"
    description: ClassVar[str] = ("The user wants to actually look into or try out an idea, or go on talking with the exploration project already opened for it (this conversation is handed straight to it, with no command for the user to type). "
                                  "args: {idea: <idea number; may be left out for the one under discussion>}")
    lookup: ClassVar[bool] = False
    acts_on: ClassVar[tuple[str, ...]] = ("idea", "conversation")  # it lends the conversation to the sandbox

    def __init__(self, ideas: Ideas):
        self._ideas = ideas

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        name = self._ideas.explore(idea_number(a, turn, self._ideas))
        return Outcome(turn.words("tools_ideas.exploring", project=self._ideas.label_of(name)), lend=name)


class PromoteIdea:
    name: ClassVar[str] = "promote_idea"
    description: ClassVar[str] = ("The user wants to make an idea a real project (with its own repo and to-dos, shown on the progress page; it was tried, it works, and the user wants to keep building it). It cannot be taken back: "
                                  "use it only when the user plainly says so. args: {idea: <idea number; may be left out for the one under discussion>, "
                                  "name: <project name, lowercase English joined with -; if the user said one, use the user's; otherwise empty>}")
    lookup: ClassVar[bool] = False
    acts_on: ClassVar[tuple[str, ...]] = ("idea",)

    def __init__(self, ideas: Ideas):
        self._ideas = ideas

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        from ..ideas import Later

        try:
            name = self._ideas.promote(idea_number(a, turn, self._ideas), str(a.get("name") or "") or None)
        except Later as later:  # its session is in the middle of a turn: done right after, without a word more
            return Outcome(turn.words("tools_ideas.promoting", name=later))
        return Outcome(turn.words("tools_ideas.promoted", name=self._ideas.label_of(name)))


class RenameIdea:
    name: ClassVar[str] = "rename_idea"
    description: ClassVar[str] = ("The user wants to give an idea (or the project made from it) another name (\"call it X\", \"change the name to X\"). "
                                  "args: {idea: <idea number or its current name; may be left out for the one under discussion>, name: <the new name, lowercase English joined with ->}")
    lookup: ClassVar[bool] = False
    acts_on: ClassVar[tuple[str, ...]] = ("idea",)

    def __init__(self, ideas: Ideas):
        self._ideas = ideas

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        name = str(a.get("name") or "").strip()
        if not name:
            raise ValueError(turn.words("tools_ideas.which_name"))
        return Outcome(turn.words("tools_ideas.renamed", name=self._ideas.rename(idea_number(a, turn, self._ideas), name)))
