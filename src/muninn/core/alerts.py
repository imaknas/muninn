"""Reminders (the inbox's alerts) as an actor: one can be the topic of a discussion,
and the user says it was seen (or taps it) to clear it; through the gate, so it can
come back. Which alerts are shown, and how they are grouped, is the views' business:
they come in as `current`."""

from __future__ import annotations

from typing import Any, Callable, Protocol, Sequence

from .actions import ActionGate, AlertsSeen, DecidedBy, Ran
from .ports import InboxStore
from .tools import Tool
from .tools.alerts import AlertSeen
from .language import Language


class AlertStorage(InboxStore, Protocol):
    """The inbox the alerts live in."""


class Alerts:
    kinds = ("alert",)

    def __init__(self, store: AlertStorage, gate: ActionGate, current: Callable[[], Sequence[dict[str, Any]]], *, words: Language):
        """`current`: the alerts shown now, one per topic (Views.alerts: id, ids, title, action, times, domain)."""
        self.words = words
        self._store = store
        self._gate = gate
        self._current = current

    def current(self) -> list[dict[str, Any]]:
        return list(self._current())

    def find(self, aid: int) -> dict[str, Any] | None:
        return next((a for a in self._current() if a["id"] == aid or aid in (a.get("ids") or [])), None)

    def seen(self, ids: Sequence[int], title: str = "") -> Ran | None:
        """The user saw these (a tap, or their words): read, undoably."""
        return self._gate.run(AlertsSeen(tuple(int(i) for i in ids), title), DecidedBy.USER) if ids else None

    # the change itself, which the gate's action makes

    def mark_read(self, ids: Sequence[int]) -> None:
        self._store.mark_read(list(ids))

    def mark_unread(self, ids: Sequence[int]) -> None:
        self._store.mark_unread(list(ids))

    def topic(self, kind: str, ref: str) -> dict[str, Any] | None:
        try:
            a = self.find(int(ref))
        except ValueError:
            return None
        if a is None:
            return None
        return {"kind": "alert", "ref": a["id"], "domain": a.get("domain"), "title": a["title"], "action": a.get("action"),
                "times": a.get("times", 1),
                "note": "This is a reminder Muninn sent. When the user says the user knows, has seen it, or needs no more reminding, use alert_seen; "
                        "to change the plan (goals, deadlines), use change_plan."}

    def tools(self) -> list[Tool]:
        return [AlertSeen(self)]
