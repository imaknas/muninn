"""Today's and this week's priorities for each domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Mapping, Sequence

from .drift import DEFAULT_THRESHOLDS, Threshold
from .language import Language
from .metrics import Metric
from .models import DomainPlan
from .ports import ObservationStore
from .progress import DeadlineStatus, GoalProgress, deadline_statuses, goal_progress
from .timewin import ActiveHours, local_date


@dataclass(frozen=True)
class Priority:
    text: str
    why: str
    priority: int
    kind: str  # deadline | deadline_target | next_action | goal_action
    target: str | None = None


@dataclass(frozen=True)
class DomainAgenda:
    plan: DomainPlan
    today: tuple[Priority, ...]
    week: tuple[Priority, ...]
    goals: tuple[GoalProgress, ...]
    deadlines: tuple[DeadlineStatus, ...]  # open ones, soonest first


@dataclass(frozen=True)
class Agenda:
    day: date
    domains: tuple[DomainAgenda, ...] = field(default_factory=tuple)


def _due_label(d: date, today: date, words: Language) -> str:
    delta = (d - today).days
    if delta < 0:
        return words("planner.overdue", days=-delta)
    if delta == 0:
        return words("planner.due_today")
    if delta == 1:
        return words("planner.due_tomorrow")
    return words("planner.due_on", due=d)


def plan_domain(
    plan: DomainPlan,
    now: datetime,
    store: ObservationStore,
    metrics: Mapping[str, Metric],
    completed: set[str],
    hours: ActiveHours,
    max_today: int = 5,
    *,
    words: Language,
) -> DomainAgenda:
    today = local_date(now, plan.timezone)
    week_end = today + timedelta(days=6 - today.weekday())
    goals = tuple(
        p for g in plan.goals if (p := goal_progress(plan, g, now, store, metrics, hours)) is not None
    )
    deadlines = sorted(
        (s for s in deadline_statuses(plan, now, completed) if not s.done),
        key=lambda s: (s.days_left, s.deadline.priority),
    )
    seen: set[str] = set()
    todays: list[Priority] = []
    weeks: list[Priority] = []

    def target_priority(t: str) -> int:
        tgt = plan.target(t)
        return tgt.priority if tgt else 3

    # 1. deadlines inside their lead window: every pending target is a today item
    for st in deadlines:
        if not st.in_lead_window:
            continue
        why = words("planner.deadline_why", title=st.deadline.title, due=_due_label(st.deadline.due, today, words))
        if st.pending_targets:
            for t in sorted(st.pending_targets, key=target_priority):
                item = plan.tracker_item(t)
                step = item.next_action if item and item.next_action else words("planner.finish")
                todays.append(Priority(words("planner.target_step", target=plan.target_title(t), step=step), why, st.deadline.priority, "deadline_target", t))
                seen.add(t)
        else:
            todays.append(Priority(st.deadline.title, why, st.deadline.priority, "deadline"))

    # 2. tracker next actions due today or overdue
    open_items = sorted(
        (i for i in plan.tracker if not i.is_closed and i.next_action and i.next_action_due),
        key=lambda i: (i.next_action_due, target_priority(i.target)),
    )
    for i in open_items:
        if i.target in seen:
            continue
        text = words("planner.target_step", target=plan.target_title(i.target), step=i.next_action)
        if i.next_action_due <= today:
            todays.append(Priority(text, _due_label(i.next_action_due, today, words), target_priority(i.target), "next_action", i.target))
            seen.add(i.target)
        elif i.next_action_due <= week_end:
            weeks.append(Priority(text, _due_label(i.next_action_due, today, words), target_priority(i.target), "next_action", i.target))
            seen.add(i.target)

    # 3. goals behind pro-rata: their first corrective action
    for p in sorted(goals, key=lambda p: p.goal.priority):
        threshold = DEFAULT_THRESHOLDS.get(p.unit, Threshold(1, 1))
        if p.met or threshold.level(p.deficit) == 0 or not p.goal.if_behind:
            continue
        todays.append(Priority(p.goal.if_behind[0], words("planner.behind", goal=p.goal.title), p.goal.priority, "goal_action"))

    # week: deadlines due this week that are not already today items
    for st in deadlines:
        if not st.in_lead_window and st.deadline.due <= week_end:
            weeks.append(Priority(st.deadline.title, _due_label(st.deadline.due, today, words), st.deadline.priority, "deadline"))

    todays.sort(key=lambda p: p.priority)
    return DomainAgenda(plan, tuple(todays[:max_today]), tuple(weeks), goals, tuple(deadlines))


def build_agenda(
    plans: Sequence[DomainPlan],
    now: datetime,
    store: ObservationStore,
    metrics: Mapping[str, Metric],
    completed: set[str],
    hours: ActiveHours,
    *,
    words: Language,
) -> Agenda:
    tz = plans[0].timezone if plans else "UTC"
    return Agenda(
        day=local_date(now, tz),
        domains=tuple(
            plan_domain(p, now, store, metrics, completed, hours, words=words) for p in sorted(plans, key=lambda p: p.priority)
        ),
    )
