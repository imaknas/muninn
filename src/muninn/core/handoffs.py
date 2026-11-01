"""Facts passed on to the projects whose to-dos they concern.

A handoff waits as 'pending' until the delivery reads it
(`muninn handoffs --json`) and marks it 'delivered', or the user cancels it.
The project reports 'done' when it has acted on it (project_messages.py).
Settled ones are kept for a while, then pruned.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from .cases import CaseFeedback
from .ports import Clock, HandoffStore

EFFECT_STRENGTH = {"relevant": 1, "changes": 2, "resolves": 3}
RECORDED = "recorded"  # told only as a Principal Handoff record (never listed as a contracts v1 handoff)
KEEP = timedelta(days=60)  # delivered and cancelled handoffs, after their last change


def stronger(new: dict[str, Any], old: dict[str, Any]) -> bool:
    return EFFECT_STRENGTH.get(new.get("effect"), 0) > EFFECT_STRENGTH.get(old.get("effect"), 0)


class Handoffs:
    def __init__(self, store: HandoffStore, clock: Clock, cases: CaseFeedback):
        self._store = store
        self._clock = clock
        self._cases = cases

    def pending(self, *, status: str | None = "pending", since: int = 0, include_held: bool = False) -> list[dict[str, Any]]:
        """Held ones (`not_before` in the future: a close Muninn inferred,
        left time to undo) are not handed out yet. A delivery that reads
        with `since` set to the highest id it has seen would skip them for
        good once they come due: deliveries read all pending ones."""
        now = self._clock.now().isoformat()
        return [h for h in self._store.handoffs(status=status, since=since)
                if include_held or not h.get("not_before") or h["not_before"] <= now]

    def same_as(self, project: str, key: str, target: dict[str, str]) -> dict[str, Any]:
        """The project's item `key` is now taken as one with `target` (another project's item {project, key}, or
        {uri}). Told as a record only (contracts v1 has no such handoff)."""
        return self.recorded(project=project, item=None, fact="", effect="same_as", reply_to=key, target=target)

    def premise(self, project: str, key: str, said: str) -> dict[str, Any]:
        """Something the project's item `key` rests on changed (the user moved its date): a note; what it asks stays
        the project's to change. Told as a record only."""
        return self.recorded(project=project, item=None, fact=said, effect="premise", reply_to=key)

    def recorded(self, **fields: Any) -> dict[str, Any]:
        """Told as a record only: what v1 never said, or says no more (a settlement back to the project that claimed
        it)."""
        h = self._store.add_handoff({**fields, "ts": self._clock.now().isoformat()}, self._clock.now(), replaces=lambda new, old: False)
        self._store.set_handoff_status(h["id"], RECORDED, self._clock.now())
        return {**h, "status": RECORDED}

    def add(self, **fields: Any) -> dict[str, Any]:
        """Told once is enough: the same pending handoff is returned, and a
        stronger effect replaces a weaker one."""
        return self._store.add_handoff(fields, self._clock.now(), replaces=stronger)

    def renamed(self, old: str, new: str) -> None:
        """What was handed to a project, under its new name."""
        self._store.rename_handoff_project(old, new)

    def get(self, hid: int) -> dict[str, Any] | None:
        return self._store.handoff(hid)

    def set_status(self, hid: int, status: str) -> bool:
        now = self._clock.now()
        h = self._store.set_handoff_status(hid, status, now)
        if h is None:
            return False
        if status == "cancelled":
            self._cases.record(f"handoff:{hid}", {"kind": "handoff_cancelled", "project": h.get("project"),
                                                  "item": h.get("item"), "effect": h.get("effect")})
        self._store.prune_handoffs(now - KEEP)
        return True
