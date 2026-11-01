"""Tools that record what the user reports: an application's status, hours, a deadline done,
and a look at the goals' progress."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable, ClassVar, Sequence

from ..actions import ActionGate, DeadlineMarkedDone, DecidedBy, HoursCorrection, TrackerStatus
from ..models import DomainPlan, Goal
from ..ports import Clock
from ..progress import GoalProgress
from . import Outcome, Turn, split_id


class SetStatus:
    name: ClassVar[str] = "set_status"
    description: ClassVar[str] = "Change an application's status. args: {target: <target id>, status: <one of statuses>}"
    lookup: ClassVar[bool] = False

    def __init__(self, plan: Callable[[str], DomainPlan | None], gate: ActionGate):
        """`plan`: a domain's confirmed plan; the change goes through the gate (the user's words: done, undoable)."""
        self._plan = plan
        self._gate = gate

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        domain, target = split_id(a["target"], turn.words)
        status = str(a["status"])
        plan = self._plan(domain)
        if plan is None or plan.tracker_item(target) is None:
            raise ValueError(turn.words("tools_records.no_target", target=a["target"]))
        ran = self._gate.run(TrackerStatus(domain, target, status, line=f"{plan.target_title(target)} → {status}"), DecidedBy.USER)
        return Outcome(ran.text, ran.id)


class LogHours:
    name: ClassVar[str] = "log_hours"
    description: ClassVar[str] = "Log hours after the fact or correct them (may be negative). args: {goal: <goal id>, hours: <number>, date: <YYYY-MM-DD or null>}"
    lookup: ClassVar[bool] = False

    def __init__(self, gate: ActionGate):
        self._gate = gate

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        if a.get("date"):
            date.fromisoformat(a["date"])  # a malformed date is refused before anything is recorded
        ran = self._gate.run(HoursCorrection(str(a["goal"]), float(a["hours"]), a.get("date") or None), DecidedBy.USER)
        if ran is None:
            raise ValueError(turn.words("tools_records.hours_not_kept"))
        return Outcome(ran.text, ran.id)


class MarkDone:
    name: ClassVar[str] = "mark_done"
    description: ClassVar[str] = "Mark a manually completed deadline as done. args: {deadline: <deadline id>}"
    lookup: ClassVar[bool] = False

    def __init__(self, plan: Callable[[str], DomainPlan | None], gate: ActionGate):
        """`plan`: a domain's plan, unconfirmed items included."""
        self._plan = plan
        self._gate = gate

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        domain, did = split_id(a["deadline"], turn.words)
        plan = self._plan(domain)
        d = next((x for x in plan.deadlines if x.id == did), None) if plan else None
        if d is None:
            raise ValueError(turn.words("tools_records.no_deadline", deadline=a["deadline"]))
        ran = self._gate.run(DeadlineMarkedDone(f"{domain}/{did}", d.title), DecidedBy.USER)
        return Outcome(ran.text, ran.id)


class Progress:
    name: ClassVar[str] = "progress"
    description: ClassVar[str] = "Look up a goal's progress. args: {goal: <goal id or null for all>}"
    lookup: ClassVar[bool] = True

    def __init__(self, plans: Callable[[], Sequence[DomainPlan]], progress: Callable[[DomainPlan, Goal, datetime], GoalProgress | None],
                 clock: Clock):
        """`plans`: the confirmed plans; `progress`: a goal's progress now."""
        self._plans = plans
        self._progress = progress
        self._clock = clock

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        from ..reports import goal_line

        now = self._clock.now()
        lines = []
        for plan in self._plans():
            for g in plan.goals:
                if a.get("goal") and a["goal"] != f"{plan.id}/{g.id}":
                    continue
                p = self._progress(plan, g, now)
                if p:
                    lines.append(goal_line(p, turn.words)[2:])
        if not lines:
            raise ValueError(turn.words("tools_records.no_goal", goal=a.get("goal")))
        return Outcome("\n".join(lines), link="#progress")


class TrackerTools:
    """The application tracker's tools."""

    def __init__(self, plan: Callable[[str], DomainPlan | None], gate: ActionGate):
        self._plan = plan
        self._gate = gate

    def tools(self) -> list[Any]:
        return [SetStatus(self._plan, self._gate)]
