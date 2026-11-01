"""The user's corrections, kept on the evaluation case that caused them.

A case is one recorded understanding call (see evals.py). What it led to (an
action, a question, a handoff) is linked to it by a ref such as
"action:<id>", "confirm:<id>" or "handoff:<id>", so that undoing, answering
or cancelling that thing later is written back onto the case.
"""

from __future__ import annotations

from typing import Any, Protocol

from .ports import Clock, EvalStore, StateStore


class CaseStorage(EvalStore, StateStore, Protocol):
    """The cases, and which case each thing Muninn did came from."""


class CaseFeedback:
    def __init__(self, store: CaseStorage, clock: Clock):
        self._store = store
        self._clock = clock

    def link(self, ref: str, case: int | None) -> None:
        if case is None:
            return
        self._store.update_state("case_links", lambda now: {**(now or {}), ref: case})

    def record(self, ref: str, feedback: dict[str, Any]) -> None:
        case = (self._store.get_state("case_links") or {}).get(ref)
        if case is not None:
            self._store.add_case_feedback(int(case), {"ts": self._clock.now().isoformat(), **feedback})
