"""Where each commitment stands right now. Shared by drift rules, the planner
and the reports so they never disagree."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Mapping

from .metrics import Metric
from .models import Deadline, DomainPlan, Goal, status_at_least
from .ports import ObservationStore
from .timewin import ActiveHours, elapsed_fraction, local_date, period_window

# windows are half-open; measuring "up to now" must include something stamped now
INCLUSIVE = timedelta(microseconds=1)


@dataclass(frozen=True)
class GoalProgress:
    plan: DomainPlan
    goal: Goal
    actual: float | None  # None: no data yet to judge by
    target: float
    expected: float  # where an at_least goal should be by now; the cap for at_most
    unit: str
    direction: str = "at_least"

    @property
    def known(self) -> bool:
        return self.actual is not None

    @property
    def deficit(self) -> float:
        """How far off track, in the metric's unit (0 when on track or unknown)."""
        if self.actual is None:
            return 0.0
        if self.direction == "at_most":
            return max(0.0, self.actual - self.target)
        return max(0.0, min(self.expected, self.target) - self.actual)

    @property
    def met(self) -> bool:
        if self.actual is None:
            return False
        if self.direction == "at_most":
            return self.actual <= self.target
        return self.actual >= self.target


def goal_progress(
    plan: DomainPlan,
    goal: Goal,
    now: datetime,
    store: ObservationStore,
    metrics: Mapping[str, Metric],
    hours: ActiveHours,
) -> GoalProgress | None:
    metric = metrics.get(goal.metric)
    if metric is None:
        return None
    start, end = period_window(goal.period, now, plan.timezone)
    actual = metric.measure(plan, goal, start, min(end, now + INCLUSIVE), store)
    if metric.accrues:
        expected = goal.target * elapsed_fraction(goal.period, now, plan.timezone, hours)
    else:
        expected = goal.target
    return GoalProgress(plan, goal, actual, goal.target, expected, metric.unit, metric.direction)


def measure_window(
    plan: DomainPlan, goal: Goal, start: datetime, end: datetime, store: ObservationStore, metrics: Mapping[str, Metric]
) -> float | None:
    metric = metrics.get(goal.metric)
    return None if metric is None else metric.measure(plan, goal, start, end, store)


@dataclass(frozen=True)
class DeadlineStatus:
    plan: DomainPlan
    deadline: Deadline
    days_left: int
    done: bool
    pending_targets: tuple[str, ...]

    @property
    def in_lead_window(self) -> bool:
        return not self.done and self.days_left <= self.deadline.lead_days

    @property
    def qualified_id(self) -> str:
        return f"{self.plan.id}/{self.deadline.id}"


def deadline_status(plan: DomainPlan, d: Deadline, today: date, completed: set[str]) -> DeadlineStatus:
    qid = f"{plan.id}/{d.id}"
    if d.done_when.manual:
        pending: tuple[str, ...] = ()
        done = qid in completed
    else:
        pending = tuple(
            t
            for t in d.done_when.targets
            if not ((item := plan.tracker_item(t)) and status_at_least(item.status, d.done_when.status_at_least))
        )
        done = not pending
    return DeadlineStatus(plan, d, (d.due - today).days, done, pending)


def deadline_statuses(plan: DomainPlan, now: datetime, completed: set[str]) -> list[DeadlineStatus]:
    today = local_date(now, plan.timezone)
    return [deadline_status(plan, d, today, completed) for d in plan.deadlines]
