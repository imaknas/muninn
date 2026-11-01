"""The user's records as an actor: an application's status in the tracker (and taking it
back), hours added to a goal's estimate, a deadline marked done. Each change refreshes what
depends on it (the observations collected, the plans evaluated)."""

from __future__ import annotations

import secrets
from datetime import date, datetime, time
from typing import Any, Callable, Protocol, Sequence

from .metrics import HOURS_CORRECTION
from .models import ALL_STATUSES, ActionRecord, Confirmation, DomainPlan, Observation, Sensitivity
from .ports import ActionLog, Clock, CompletionStore, PlanRepo, StatusChangeLog
from .timewin import local, local_date
from .language import Language


class RecordStorage(StatusChangeLog, CompletionStore, ActionLog, Protocol):
    """The status changes (for undo), the deadlines marked done, and the answers logged."""


class Records:
    def __init__(self, store: RecordStorage, clock: Clock, tz: str, repo: Callable[[], PlanRepo],
                 plan: Callable[[str], DomainPlan | None], ingest: Callable[[Sequence[Observation]], list[Observation]],
                 collect: Callable[[], Any], evaluate: Callable[[], Any], *, words: Language):
        """`repo`: the plans as stored (the tracker is written there); `plan`: a domain's plan,
        unconfirmed items included; `ingest` stores (and attributes) observations; `collect` and
        `evaluate` refresh what a change affects."""
        self.words = words
        self._store = store
        self._clock = clock
        self._tz = tz
        self._repo = repo
        self._plan = plan
        self._ingest = ingest
        self._collect = collect
        self._evaluate = evaluate

    def set_status(self, domain: str, target: str, status: str) -> int:
        if status not in ALL_STATUSES:
            raise ValueError(f"unknown status {status}")
        now = self._clock.now()
        applied = local_date(now, self._tz) if status == "applied" else None
        before = self._repo().set_tracker_status(domain, target, status, applied)
        after = {"status": status, "applied": before.get("applied") or (applied.isoformat() if applied else None)}
        change = self._store.record_status_change(domain, target, before, after, now)
        self._collect()
        self._evaluate()
        return change

    def undo_status(self, change_id: int) -> bool:
        ch = self._store.status_change(change_id)
        if not ch:
            return False
        before = ch["before"]
        applied = date.fromisoformat(before["applied"]) if before.get("applied") else None
        self._repo().set_tracker_status(ch["domain"], ch["target"], before["status"], applied, replace_applied=True)
        self._store.record_status_change(ch["domain"], ch["target"], ch["after"], before, self._clock.now())
        self._collect()
        return True

    def correct_hours(self, goal: str, hours: float, day: date | None, note: str = "") -> Observation | None:
        """Signed delta on top of the estimate; `goal` is "<domain>/<goal>"."""
        domain, _, gid = goal.partition("/")
        plan = self._plan(domain)
        if not plan or not plan.goal(gid):
            raise ValueError(f"unknown goal {goal}")
        now = self._clock.now()
        ts = now
        if day is not None and day != local_date(now, plan.timezone):
            ts = datetime.combine(day, time(12, 0), local(now, plan.timezone).tzinfo)
        key = f"hours:{goal}:{now.isoformat()}:{secrets.token_hex(4)}"  # each correction is its own entry
        payload = {"hours": float(hours), "note": note}
        new = self._ingest([Observation("pwa", ts, HOURS_CORRECTION, key, payload, domain, goal, Sensitivity.PERSONAL)])
        return new[0] if new else None

    def set_done(self, qualified_id: str, done: bool = True) -> None:
        self._store.set_completed(qualified_id, done, self._clock.now())
        self._evaluate()

    def deadline_answered(self, c: Confirmation, answer: str, confirm: Callable[[str], None]) -> None:
        """Yes to "is this deadline done?" (asked from news the user gave): marked done, undoable;
        `confirm(qualified id)` settles it in the plan review too (it was real)."""
        if answer != c.options[0]:
            return
        qid = c.data["deadline"]
        self.set_done(qid)
        confirm(qid)
        domain = qid.partition("/")[0]
        # these cards were stored in Chinese before the action gate existed: their question is read as written
        title = c.question.split("」")[0].lstrip("「")
        self._store.log_action(ActionRecord(None, self._clock.now(), domain, self.words("records.done", title=title),
                                            {"kind": "deadline_done", "id": qid}))

    def status_answered(self, c: Confirmation, answer: str) -> None:
        """Yes to a status change read from a mail (the cards from before the action gate)."""
        if answer != c.options[0]:
            return
        change = self.set_status(c.data["domain"], c.data["target"], c.data["status"])
        # a card stored in Chinese before the action gate existed: its first sentence names the change
        what = c.question.split("。")[0]
        self._store.log_action(ActionRecord(None, self._clock.now(), c.data["domain"], self.words("records.status_changed", what=what),
                                            {"kind": "status_change", "id": change}))
