"""What Muninn does next with an idea, without being told: an idea is a topic to move forward, not a card to
decide on. One of: talk it through further, try it out, offer to try it out, make it a project, or set it aside.

In a discussion the routing reply says it (its `next`, written with the reply, so the reply knows what is about to
happen); after an experiment comes back, it is asked on its own (idea_next), since no reply is being written.
Trying it out and setting it aside are done, then said (they can be taken back); offering to try it out is a
button. Making it a project is a bigger change: Muninn asks once, in one sentence, with a button, and the
conversation is marked so it is not asked again. What is done goes through the ideas' own tools, so lending the
conversation to the sandbox, undo and the rest are what they always are."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Collection, Sequence

from .decide import Decider, Question
from .models import Sensitivity
from .ports import Clock, StateStore
from .language import Language

NEXT_STEPS = ("discuss", "experiment", "offer_experiment", "promote", "shelve")
LABELS = ("discuss", "promote")  # after an experiment came back: ask to make it a project, or leave it be
LINES = 8  # the conversation's latest lines the question reads
ASKED = "asked_promote"  # the mark of a conversation that asked to make its idea a project
QUEUE = "ideas:returned"  # experiments that came back, not reported on yet

SYSTEM = """An experiment on one of the user's ("the user") ideas just came back. You decide whether the assistant ("you") should now
ask the user to make it a real project. Input is JSON: "idea" (the idea, its status, the project it was tried out in),
"conversation" (the latest lines, oldest first), and "returned" (what the experiment reported).
Reply with one JSON object and nothing else: {"next": "promote" | "discuss", "why": "<a few words>"}
- promote: it worked, and the conversation shows the user wants to keep building it.
- discuss: anything else (it did not work, it is unclear, or the user has not said the user wants more); when unsure, discuss."""


def idea_of(about: dict[str, Any] | None) -> int | None:
    if not about or about.get("kind") != "idea":
        return None
    try:
        return int(about.get("ref"))
    except (TypeError, ValueError):
        return None


def question(called: str, words: Language) -> str:
    return words("idea_next.promote_question", called=called)


def button(n: int, words: Language) -> dict[str, Any]:
    return {"label": words("idea_next.promote_button"), "tool": "promote_idea", "args": {"idea": n}}


def offer(n: int, words: Language) -> dict[str, Any]:
    return {"label": words("idea_next.explore_button"), "tool": "explore_idea", "args": {"idea": n}}


class IdeaNext:
    def __init__(self, decider: Decider, idea: Callable[[int], dict[str, Any] | None], *, words: Language):
        """`idea(n)`: the idea as the model sees it in a discussion (its topic), None when there is none."""
        self.words = words
        self._decider = decider
        self._idea = idea

    def apply(self, plan: dict[str, Any], said: str, *, acted: bool, marks: Collection[str]) -> None:
        """Carries out the routing reply's `next` for a round about an idea: an action, a button, or the question
        to make it a project (then `plan["mark"]`). Nothing when the round already settled the idea itself
        (`acted`) or the round is not about one."""
        n, step = idea_of(plan.get("about")), plan.pop("next", None)
        if n is None or acted or step in (None, "discuss") or (self._idea(n) or {}).get("status") == "promoted":
            return  # one made a project or a domain already goes on there
        if step == "experiment":
            plan["actions"].append({"tool": "explore_idea", "args": {"idea": n}})
        elif step == "offer_experiment":
            plan["suggest"] = [offer(n, self.words), *[s for s in plan.get("suggest") or [] if s.get("tool") != "explore_idea"]][:3]
        elif step == "shelve":
            plan["actions"].append({"tool": "settle_idea", "args": {"idea": n, "decision": "shelve", "why": said[:200]}})
        elif step == "promote" and ASKED not in marks:
            plan["reply"] = "\n".join(x for x in (plan.get("reply"), question(self.called(n), self.words)) if x)
            plan["suggest"] = [button(n, self.words), *[s for s in plan.get("suggest") or [] if s.get("tool") != "promote_idea"]][:3]
            plan["mark"] = [*plan.get("mark", []), ASKED]

    def returned(self, n: int, turns: Sequence[dict[str, Any]], report: str, marks: Collection[str]) -> tuple[str, list[dict[str, Any]]] | None:
        """An experiment on idea `n` came back with `report`: what Muninn says about it next and its buttons,
        or None when there is nothing more to say than the report itself (or it asked already)."""
        idea = self._idea(n)
        if idea is None or ASKED in marks:
            return None
        content = {"idea": {k: idea[k] for k in ("title", "status", "explored_in") if k in idea},
                   "conversation": [{"who": "the user" if x.get("role") == "user" else "you", "text": str(x.get("text") or "")}
                                    for x in turns[-LINES:]],
                   "returned": report}
        verdict = self._decider.decide(Question(
            point="idea_next", system=SYSTEM, content=json.dumps(content, ensure_ascii=False), labels=LABELS,
            sensitivity=Sensitivity.SENSITIVE, domain=None, field="next"))
        return (question(self.called(n), self.words), [button(n, self.words)]) if verdict is not None and verdict.label == "promote" else None

    def called(self, n: int) -> str:
        """What the user calls idea `n`."""
        return str((self._idea(n) or {}).get("name") or f"idea-{n}")


class IdeaReports:
    """An experiment that came back is reported where its idea is talked about: the handover has joined the
    conversation already; the phone is told, and when what came back makes the idea worth a real project,
    Muninn asks so there (once). Noted where the thread is written; reported outside it, since it asks a model."""

    def __init__(self, store: StateStore, clock: Clock, next_step: IdeaNext, idea_of_project: Callable[[str], int | None],
                 thread: Callable[[str], dict[str, Any] | None], speak: Callable[[str, str, list[dict[str, Any]], str], Any],
                 quiet: Callable[[datetime], bool], push: Callable[[str], Any], *, words: Language):
        """`idea_of_project(name)`: the idea a sandbox was opened for; `speak(thread, text, buttons, mark)`: Muninn says
        it there and marks the conversation; `push(title)`: tells the phone (what it says is in the conversation)."""
        self.words = words
        self._store = store
        self._clock = clock
        self._next = next_step
        self._idea_of = idea_of_project
        self._thread = thread
        self._speak = speak
        self._quiet = quiet
        self._push = push

    def returned(self, lease: dict[str, Any], report: str) -> None:
        n = self._idea_of(str(lease.get("project") or ""))
        if n is not None:
            queued = list(self._store.get_state(QUEUE) or [])
            self._store.set_state(QUEUE, queued + [{"thread": lease["thread"], "idea": n, "report": report}])

    def _take(self) -> list[dict[str, Any]]:
        queued = list(self._store.get_state(QUEUE) or [])
        if queued:
            self._store.set_state(QUEUE, [])
        return queued

    def run(self, locked: Callable[[Callable[[], Any]], Any]) -> int:
        """Reports what came back; `locked(fn)` runs fn where threads are written. How many were reported."""
        reported = 0
        for item in locked(self._take):
            t = self._thread(item["thread"]) or {}
            follow = self._next.returned(int(item["idea"]), t.get("turns") or [], str(item["report"]), t.get("marks") or [])
            if follow is not None:
                text, buttons = follow
                locked(lambda: self._speak(item["thread"], text, buttons, ASKED))
            if not self._quiet(self._clock.now()):
                self._push(self.words("idea_next.returned_push", called=self._next.called(int(item["idea"]))))
            reported += 1
        return reported
