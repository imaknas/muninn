"""Ideas as an actor: a thought the user says in passing that belongs to none of the
plans yet (something to make, a direction to look into). Captured by saying so,
through the gate so it can be taken back, and one can be the topic of a
discussion. An idea has no domain, so what is said about it stays local like
anything else without one (when routing keeps such things local).

An idea ends in a decision, or it only piles up: set aside (it stays listed, out of
the way), dropped, promoted to a domain (the plans make the domain and tell the
idea; undoing the domain puts the idea back), or made a project of its own (the
project it was tried out in becomes a real one, with its history; this cannot be
taken back from here). Exploring one opens a project to look into it in, where
this side can (Sandboxes); where it cannot, it says so.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Protocol

from .actions import ActionGate, DecidedBy, IdeaCaptured, IdeaSettled, Ran
from .ports import Clock, SandboxRefused, StateStore
from .tools import Tool
from .tools.ideas import CaptureIdea, ExploreIdea, PromoteIdea, RenameIdea, SettleIdea
from .language import Language

TEXT_CHARS = 500
WAITING = "ideas:to_promote"  # ideas to make projects once their sandbox's session is done with its turn: {n: name}
TRIES_TAKEN = 5  # names tried for a sandbox when another project has the idea's (sofa-fit, sofa-fit-2, …)
RENAMING = "ideas:to_rename"  # sandboxes and projects to rename once their session is done with its turn: {n: name}


class Later(Exception):
    """It will be done, a little later (the sandbox's session is in the middle of a turn)."""
OPEN, SHELVED, DROPPED, PROMOTED = "open", "shelved", "dropped", "promoted"
SOURCE = "idea:"  # how a domain grown out of an idea names it
PROJECT_NAME = re.compile(r"(?!.*--)[a-z0-9](?:[a-z0-9-]{0,38}[a-z0-9])?")  # what a project may be called


class IdeaStorage(StateStore, Protocol):
    """The ideas (state "ideas"), oldest first."""


def status(idea: dict[str, Any]) -> str:
    return idea.get("status") or OPEN


class Ideas:
    kinds = ("idea",)

    def __init__(self, store: IdeaStorage, clock: Clock, gate: ActionGate,
                 sandbox: Callable[[str, str], str | None] = lambda name, purpose: None,
                 upgrade: Callable[[str, str | None], str | None] = lambda name, rename: None,
                 renamed: Callable[[str, str], None] = lambda old, new: None,
                 rename_project: Callable[[str, str], str | None] = lambda name, to: None, *, words: Language):
        """`sandbox(name, purpose)`: opens a project to explore an idea in, its name (lent like any
        project), or None when this side cannot; `upgrade(name, rename)`: makes that project a real one
        (renamed when asked), its name then, or None when this side cannot; `renamed(old, new)`: a sandbox or the
        project made of it goes by a new name (what was lent to it goes on under that name); `rename_project(name,
        to)`: renames a sandbox or project, its name then, or None when this side cannot."""
        self.words = words
        self._store = store
        self._clock = clock
        self._gate = gate
        self._sandbox = sandbox
        self._upgrade = upgrade
        self._renamed = renamed
        self._rename_project = rename_project

    def all(self) -> list[dict[str, Any]]:
        return list(self._store.get_state("ideas") or [])

    def listed(self) -> list[dict[str, Any]]:
        """What is still the user's to think about: open ones and those set aside (oldest first)."""
        return [i for i in self.all() if status(i) in (OPEN, SHELVED)]

    def get(self, n: int) -> dict[str, Any] | None:
        return next((i for i in self.all() if i["n"] == n), None)

    def of_sandbox(self, name: str) -> int | None:
        """The idea a sandbox was opened for (or a project made of one)."""
        return next((i["n"] for i in self.all() if name in (i.get("sandbox"), i.get("project"))), None)

    def aliases(self, project: str) -> set[str]:
        """Every name a project has gone by: for an idea's, its first sandbox name, its sandbox, the project made of it."""
        n = self.of_sandbox(project)
        idea = self.get(n) if n is not None else None
        if idea is None:
            return {project}
        return {project, f"idea-{idea['n']}", *(x for x in (idea.get("sandbox"), idea.get("project")) if x)}

    def label_of(self, project: str) -> str:
        """What the user calls a project: an idea's sandbox, or the project made of it, by the idea's name (the
        platform may still know it by its first, numbered name); any other by its own."""
        n = self.of_sandbox(project)
        idea = self.get(n) if n is not None else None
        return name_of(idea) if idea else project

    def aka(self, about: dict[str, Any]) -> str | None:
        """What the user calls the idea a conversation is about, besides its words: its name (and its number)."""
        idea = self._of_about(about)
        return f"{name_of(idea)}, idea {idea['n']}" if idea else None

    def named(self, said: str) -> dict[str, Any] | None:
        """The idea a sentence names by its name, as what a conversation is about."""
        n = self.by_name(said)
        return {"kind": "idea", "id": str(n)} if n is not None else None

    def _of_about(self, about: dict[str, Any]) -> dict[str, Any] | None:
        if about.get("kind") != "idea":
            return None
        try:
            return self.get(int(about["id"]))
        except (KeyError, TypeError, ValueError):
            return None

    def by_name(self, said: str) -> int | None:
        """The idea called `said`, or that a sentence names by its name (「shelf-planner 那個」: a name of several
        words, so a word said in passing is not taken for one)."""
        low = said.strip().lower()
        ideas = [i for i in self.all() if i.get("name")]
        return next((i["n"] for i in ideas if i["name"] == low), None) or next(
            (i["n"] for i in ideas if "-" in i["name"] and re.search(rf"(?<![a-z0-9-]){re.escape(i['name'])}(?![a-z0-9-])", low)), None)

    def capture(self, text: str, name: str | None = None) -> tuple[Ran, int] | None:
        """The user said an idea (their words): kept, undoably, with a name to know it by (`name` when it is a
        good one and free, else one made from it); and its number."""
        text = text.strip()[:TEXT_CHARS]
        r = self._gate.run(IdeaCaptured(text, (name or "").strip().lower()), DecidedBy.USER) if text else None
        return (r, int(self._store.get_state("ideas_last_n"))) if r is not None else None

    def rename(self, n: int, name: str) -> str:
        """The idea is called `name` from now on, wherever it shows, and so are its sandbox and the project made of
        it (renamed where they live too; once their session is done with its turn when it is in the middle of one);
        the name it got. ValueError when it is not a name, or that side refused it."""
        got = self._name(n, name)
        idea = self.get(n) or {}
        there, to = idea.get("project") or idea.get("sandbox"), None
        if there and there != got:
            try:
                to = self._rename_project(there, got)
            except SandboxRefused as e:
                if e.kind != "busy":
                    raise ValueError(refused(e, self.words, "rename")) from e
                self._store.set_state(RENAMING, {**(self._store.get_state(RENAMING) or {}), str(n): got})
        self._update(n, name=got)  # before its talks are told, so they are shown by it
        if there:
            self._moved(n, there, to or there)
        return got

    def _name(self, n: int, name: str) -> str:
        """The name idea `n` can be called by (another idea's: with a number after it); ValueError when it is none."""
        if self.get(n) is None:
            raise ValueError(self.words("ideas.which_idea"))
        want = name.strip().lower()
        if not PROJECT_NAME.fullmatch(want):
            raise ValueError(self.words("ideas.bad_name", name=name))
        return self._free(want, n)

    def _moved(self, n: int, old: str, new: str) -> None:
        """Its sandbox (or the project made of it) goes by `new` now (the same: only what it is called changed)."""
        idea = self.get(n) or {}
        self._update(n, sandbox=new, **({"project": new} if idea.get("project") else {}))
        self._renamed(old, new)

    def _free(self, name: str, n: int) -> str:
        """`name`, or with a number after it when another idea has it."""
        taken = {name_of(i) for i in self.all() if i["n"] != n}
        got, k = name, 2
        while got in taken:
            got, k = f"{name[:37].rstrip('-')}-{k}", k + 1
        return got

    def settle(self, n: int, decision: str, why: str = "") -> Ran | None:
        """The user set an idea aside, dropped it, or took it back up: undoably."""
        idea = self.get(n)
        if idea is None:
            raise ValueError(self.words("ideas.which_idea"))
        if decision not in (OPEN, SHELVED, DROPPED):
            raise ValueError(self.words("ideas.which_decision"))
        if status(idea) == PROMOTED:  # a project now: it is not set aside or dropped as an idea
            raise ValueError(self.words("ideas.already_project", name=name_of(idea)))
        if status(idea) == decision:
            return None
        return self._gate.run(IdeaSettled(n, decision, idea["text"], why.strip()[:200]), DecidedBy.USER)

    def explore(self, n: int) -> str:
        """A project to look into the idea in; ValueError when this side cannot open one."""
        idea = self.get(n)
        if idea is None:
            raise ValueError(self.words("ideas.which_idea"))
        if idea.get("sandbox"):
            return idea["sandbox"]
        try:
            name = self._open_named(n, idea)
        except SandboxRefused as e:
            raise ValueError(refused(e, self.words)) from e
        if name is None:
            raise ValueError(self.words("ideas.no_sandbox_yet"))
        self._update(n, sandbox=name)
        return name

    def _open_named(self, n: int, idea: dict[str, Any]) -> str | None:
        """Its sandbox, under the name the user knows it by; when another project has that name, under the same
        name with a number after it, which the idea is called from then on (so what the screens say and where it
        lives agree); a runner that takes numbers only: idea-N."""
        called = name_of(idea)
        for k in range(1, TRIES_TAKEN + 1):
            want = called if k == 1 else f"{called[:37].rstrip('-')}-{k}"
            try:
                got = self._sandbox(want, idea["text"])
            except SandboxRefused as e:
                if e.kind == "bad_name" and called != f"idea-{n}":
                    return self._sandbox(f"idea-{n}", idea["text"])
                if e.kind != "taken" or k == TRIES_TAKEN:
                    raise
                continue
            if want != called:
                self._update(n, name=self._free(want, n))
            return got
        return None

    def promote(self, n: int, name: str | None = None) -> str:
        """The project the idea was tried out in becomes a real one (opened first when it was never tried);
        its name. ValueError when this side cannot."""
        idea = self.get(n)
        if idea is None:
            raise ValueError(self.words("ideas.which_idea"))
        if idea.get("project"):
            return idea["project"]
        if (name or "").strip():
            self._update(n, name=self._name(n, name))  # the project is called what the idea is called
        sandbox = self.explore(n)
        called = name_of(self.get(n) or idea)
        rename = called if called != sandbox else None
        try:
            got = self._upgrade(sandbox, rename if rename != sandbox else None)
        except SandboxRefused as e:
            if e.kind == "busy":  # done once its session is done with this turn (retry_waiting), unseen
                self._store.set_state(WAITING, {**(self._store.get_state(WAITING) or {}), str(n): rename})
                raise Later(called) from e
            raise ValueError(refused(e, self.words, "promote")) from e
        if got is None:
            raise ValueError(self.words("ideas.no_promote_here", sandbox=sandbox))
        self._promoted(n, sandbox, got)
        return got

    def _promoted(self, n: int, sandbox: str, got: str) -> None:
        self._update(n, status=PROMOTED, project=got, sandbox=got)  # talked to by its new name from now on
        if got != sandbox:
            self._renamed(sandbox, got)

    def retry_waiting(self, locked: Callable[[Callable[[], Any]], Any]) -> list[tuple[int, str]]:
        """The ideas waiting to be renamed or made projects, tried again (their session may be done with its turn
        by now): done quietly when it goes through; what could not be, with why. Calls the runner outside `locked`
        (which runs a change where the store is written)."""
        failed: list[tuple[int, str]] = []
        for key, to in dict(locked(lambda: self._store.get_state(RENAMING) or {})).items():
            n = int(key)
            idea = locked(lambda: self.get(n)) or {}
            there = idea.get("project") or idea.get("sandbox")
            if not there or there == to or name_of(idea) != to:  # gone, done, or renamed again since
                locked(lambda: self._drop(RENAMING, n))
                continue
            try:
                got = self._rename_project(there, to)
            except SandboxRefused as e:
                if e.kind == "busy":
                    continue
                locked(lambda: self._drop(RENAMING, n))
                failed.append((n, refused(e, self.words, "rename")))
                continue
            locked(lambda: (self._drop(RENAMING, n), self._moved(n, there, got) if got else None))
        for key, rename in dict(locked(lambda: self._store.get_state(WAITING) or {})).items():
            n = int(key)
            idea = locked(lambda: self.get(n))
            if idea is None or idea.get("project") or not idea.get("sandbox"):
                locked(lambda: self._drop(WAITING, n))
                continue
            try:
                got = self._upgrade(idea["sandbox"], rename if rename != idea["sandbox"] else None)
            except SandboxRefused as e:
                if e.kind == "busy":
                    continue
                locked(lambda: self._drop(WAITING, n))
                failed.append((n, refused(e, self.words, "promote")))
                continue
            locked(lambda: (self._drop(WAITING, n), self._promoted(n, idea["sandbox"], got) if got else None))
        return failed

    def _drop(self, key: str, n: int) -> None:
        self._store.set_state(key, {k: v for k, v in (self._store.get_state(key) or {}).items() if k != str(n)})

    # the changes themselves, made by the gate's actions or told by the plans

    def add(self, text: str, name: str | None = None) -> dict[str, Any]:
        """A new idea with a number never used before (an idea taken back keeps its number used:
        a sandbox may already be named after it), and a name to know it by: `name` when it is a good one, else
        one made from its words; with a number after it when another idea has it."""
        ideas = self.all()
        n = max([self._store.get_state("ideas_last_n") or 0, *(i["n"] for i in ideas)]) + 1
        given = name if name and PROJECT_NAME.fullmatch(name) else made_name(text) or f"idea-{n}"
        idea = {"n": n, "text": text, "ts": self._clock.now().isoformat(), "name": self._free(given, n)}
        self._store.set_state("ideas", ideas + [idea])
        self._store.set_state("ideas_last_n", n)
        return idea

    def forget(self, n: int) -> None:
        self._store.set_state("ideas", [i for i in self.all() if i["n"] != n])

    def set_status(self, n: int, to: str, why: str = "") -> str:
        """The idea's status now `to`; returns what it was."""
        idea = self.get(n)
        if idea is None:
            raise KeyError(n)
        self._update(n, status=to, why=why or None)
        return status(idea)

    def promoted(self, domain: str, source: str) -> None:
        """A domain was made out of this idea (the plans say so)."""
        if (n := _idea_of(source)) is not None and self.get(n) is not None:
            self._update(n, status=PROMOTED, domain=domain)

    def unpromoted(self, domain: str, source: str) -> None:
        """That domain was taken back: the idea is open again."""
        if (n := _idea_of(source)) is not None and self.get(n) is not None:
            self._update(n, status=OPEN, domain=None)

    def _update(self, n: int, **fields: Any) -> None:
        def changed(i: dict[str, Any]) -> dict[str, Any]:
            return {k: v for k, v in {**i, **fields}.items() if v is not None}

        self._store.set_state("ideas", [changed(i) if i["n"] == n else i for i in self.all()])

    def topic(self, kind: str, ref: str) -> dict[str, Any] | None:
        try:
            idea = self.get(int(ref))
        except ValueError:
            return None
        if idea is None:
            return None
        base = {"kind": "idea", "ref": idea["n"], "name": name_of(idea), "domain": None, "since": idea["ts"][:10], "status": status(idea)}
        if status(idea) == PROMOTED and idea.get("project"):  # what it is now: a project of its own, worked on there
            return {**base, "title": name_of(idea), "idea": idea["text"], "project": idea["project"],
                    "note": f"This was an idea the user noted down; it has been made into the project {name_of(idea)}, with its own repo "
                            "and to-dos, and it is worked on there. To talk with it there or see how far it has got, use talk_to_project "
                            "or ask_project."}
        if status(idea) == PROMOTED and idea.get("domain"):  # grown into a domain: its plan and progress are the domain's
            return {**base, "title": idea["text"], "grown_into": idea["domain"],
                    "note": f"This was an idea the user noted down; it has grown into a long-term domain ({idea['domain']}), and its plan "
                            "and progress are in that domain."}
        return {**base, "title": idea["text"], **({"explored_in": name_of(idea)} if idea.get("sandbox") else {}),  # by the name the user knows it by
                "note": "This is an idea the user mentioned before, not yet part of any plan. Help the user think it through: what it would "
                        "solve, whether it is feasible, how much time it would take; until the user says the user will do it, do not schedule "
                        "time or create tasks for the user. Whether to try it next or set it aside is yours to judge, written in next; "
                        "when the user plainly says to set it aside for now or drop it, use settle_idea; when the user plainly says to go try it, use explore_idea; "
                        "when the user says to make it a real project (with its own repo and to-dos), use promote_idea; "
                        "to make it a long-term domain (with a plan and progress, like a job search or fitness), use propose_domain."}

    def tools(self) -> list[Tool]:
        return [CaptureIdea(self), SettleIdea(self), ExploreIdea(self), PromoteIdea(self), RenameIdea(self)]


def name_of(idea: dict[str, Any]) -> str:
    """What the user calls an idea (one kept before ideas had names: by its number)."""
    return str(idea.get("name") or f"idea-{idea['n']}")


def made_name(text: str) -> str | None:
    """A name made from an idea's own words when none was given: its latin words, a few of them (none when there
    are not two: one word alone, like 「3D」, says too little to know it by)."""
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9]*|[0-9]+[A-Za-z]+", text)][:3]
    got = "-".join(words)
    return got if len(words) >= 2 and PROJECT_NAME.fullmatch(got) else None


def _idea_of(source: str) -> int | None:
    if not source.startswith(SOURCE):
        return None
    try:
        return int(source[len(SOURCE):])
    except ValueError:
        return None


def refused(e: SandboxRefused, words: Language, doing: str = "explore") -> str:
    """Why the projects side refused `doing` (explore, rename or promote), said to the user."""
    return words("ideas.refused", kind=e.kind, doing=doing, limit=e.limit, current=e.current, detail=e.detail)
