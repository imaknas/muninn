"""Deterministic drift rules. No LLM is involved in deciding what is wrong."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Mapping, Protocol, Sequence

from .metrics import PASSIVE_ACTIVITY_KINDS, Metric
from .models import DomainPlan, Signal
from .ports import ObservationStore
from .progress import GoalProgress, deadline_statuses, goal_progress
from .timewin import ActiveHours, in_window, local, local_date
from .language import Language


@dataclass(frozen=True)
class EvalContext:
    now: datetime
    plans: Sequence[DomainPlan]
    store: ObservationStore
    metrics: Mapping[str, Metric]
    completed: set[str]
    hours: ActiveHours = field(default_factory=ActiveHours)
    words: Language = field(kw_only=True)


class DriftRule(Protocol):
    def evaluate(self, ctx: EvalContext) -> list[Signal]: ...


def fmt_amount(value: float, unit: str, words: Language) -> str:
    if unit == "h":
        return f"{value:.1f}h"
    if unit == "min":
        return words("drift.minutes", value=value)
    if unit == "steps":
        return words("drift.steps", value=value)
    return f"{round(value, 1):g}"


def progress_title(p: GoalProgress, words: Language) -> str:
    a = fmt_amount(p.actual or 0.0, p.unit, words)
    t = fmt_amount(p.target, p.unit, words)
    if p.direction == "at_most":
        return words("drift.at_most", goal=p.goal.title, actual=a, target=t)
    if p.goal.period == "week":
        return words("drift.this_week", goal=p.goal.title, actual=a, target=t)
    return words("drift.today", goal=p.goal.title, actual=a, target=t)


@dataclass(frozen=True)
class Threshold:
    """Alert once off track by `first`; the level rises every `step` beyond."""

    first: float
    step: float

    def level(self, deficit: float) -> int:
        return 0 if deficit < self.first else 1 + int((deficit - self.first) // self.step)


DEFAULT_THRESHOLDS = {
    "count": Threshold(1, 1),
    "h": Threshold(1, 1),
    "min": Threshold(1, 30),
    "steps": Threshold(1500, 1500),
}


@dataclass(frozen=True)
class GoalBehindRule:
    """Behind the pro-rata target (accruing metrics), short of it (single
    readings), or over an at-most limit."""

    thresholds: Mapping[str, Threshold] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))

    def evaluate(self, ctx: EvalContext) -> list[Signal]:
        out = []
        for plan in ctx.plans:
            for goal in plan.goals:
                p = goal_progress(plan, goal, ctx.now, ctx.store, ctx.metrics, ctx.hours)
                if p is None or not p.known or p.met:
                    continue
                level = self.thresholds.get(p.unit, Threshold(1, 1)).level(p.deficit)
                if level == 0:
                    continue
                detail = ""
                if p.direction == "at_least" and p.expected < p.target:
                    detail = ctx.words("drift.on_pace", expected=fmt_amount(p.expected, p.unit, ctx.words))
                out.append(
                    Signal(
                        key=f"{plan.id}:goal:{goal.id}",
                        domain=plan.id,
                        kind="goal_behind",
                        priority=goal.priority,
                        level=level,
                        title=progress_title(p, ctx.words),
                        detail=detail,
                        actions=goal.if_behind,
                        sensitivity=plan.sensitivity_default,
                        domain_priority=plan.priority,
                        bypass_quiet=p.direction == "at_most",
                    )
                )
        return out


@dataclass(frozen=True)
class DeadlineRule:
    """Inside lead_days of the due date (or past it) and not done."""

    def evaluate(self, ctx: EvalContext) -> list[Signal]:
        out = []
        for plan in ctx.plans:
            for st in deadline_statuses(plan, ctx.now, ctx.completed):
                if not st.in_lead_window:
                    continue
                d = st.deadline
                if st.days_left < 0:
                    title = ctx.words("drift.overdue", title=d.title, days=-st.days_left, due=d.due)
                elif st.days_left == 0:
                    title = ctx.words("drift.due_today", title=d.title)
                else:
                    title = ctx.words("drift.days_left", title=d.title, days=st.days_left, due=d.due)
                actions: list[str] = []
                for t in st.pending_targets:
                    item = plan.tracker_item(t)
                    step = item.next_action if item and item.next_action else ctx.words("drift.finish_this")
                    actions.append(ctx.words("drift.target_step", target=plan.target_title(t), step=step))
                if d.done_when.manual:
                    actions.append(ctx.words("drift.mark_done"))
                out.append(
                    Signal(
                        key=f"{plan.id}:deadline:{d.id}",
                        domain=plan.id,
                        kind="deadline",
                        priority=d.priority,
                        level=d.lead_days - st.days_left + 1,
                        title=title,
                        detail=ctx.words("drift.to_go", n=len(st.pending_targets)) if st.pending_targets else "",
                        actions=tuple(actions),
                        sensitivity=plan.sensitivity_default,
                        domain_priority=plan.priority,
                    )
                )
        return out


@dataclass(frozen=True)
class OverdueActionRule:
    """Tracker rows whose next_action_due has passed while still open."""

    def evaluate(self, ctx: EvalContext) -> list[Signal]:
        out = []
        for plan in ctx.plans:
            today = local_date(ctx.now, plan.timezone)
            overdue = sorted(
                (i for i in plan.tracker if not i.is_closed and i.next_action_due and i.next_action_due < today),
                key=lambda i: i.next_action_due,
            )
            if not overdue:
                continue
            out.append(
                Signal(
                    key=f"{plan.id}:overdue-actions",
                    domain=plan.id,
                    kind="overdue_action",
                    priority=2,
                    level=len(overdue),
                    title=ctx.words("drift.todos_overdue", n=len(overdue)),
                    actions=tuple(ctx.words("drift.target_step", target=plan.target_title(i.target), step=i.next_action)
                                  for i in overdue[:5]),
                    sensitivity=plan.sensitivity_default,
                    domain_priority=plan.priority,
                )
            )
        return out


@dataclass(frozen=True)
class LateNightRule:
    """Passive activity inside the night window. Stands down when some plan
    has its own late_activity goal, which covers the same ground."""

    start: time = time(0, 30)
    end: time = time(5, 0)
    tz: str = "UTC"
    lookback: timedelta = timedelta(hours=24)

    def evaluate(self, ctx: EvalContext) -> list[Signal]:
        if any(g.metric == "late_activity" for p in ctx.plans for g in p.goals):
            return []
        obs = ctx.store.observations(ctx.now - self.lookback, ctx.now, kinds=PASSIVE_ACTIVITY_KINDS)
        late = [o for o in obs if in_window(local(o.ts, self.tz).time(), self.start, self.end)]
        if not late:
            return []
        latest = max(late, key=lambda o: o.ts)
        night = local_date(latest.ts - timedelta(hours=6), self.tz)  # 02:00 belongs to the evening before
        domain = Counter(o.domain or "muninn" for o in late).most_common(1)[0][0]
        prio = next((p.priority for p in ctx.plans if p.id == domain), 2)
        return [
            Signal(
                key=f"anomaly:late-night:{night.isoformat()}",
                domain=domain,
                kind="anomaly",
                priority=2,
                level=1,
                title=ctx.words("drift.late_night", time=f"{local(latest.ts, self.tz):%H:%M}"),
                actions=(ctx.words("drift.stop_earlier"),),
                sensitivity=max(o.sensitivity for o in late),
                domain_priority=prio,
            )
        ]


@dataclass(frozen=True)
class IdleDomainRule:
    """A domain with goals whose observed activity stopped N days ago."""

    default_days: int | None = 3

    def evaluate(self, ctx: EvalContext) -> list[Signal]:
        out = []
        for plan in ctx.plans:
            days = plan.idle_alert_days if plan.idle_alert_days is not None else self.default_days
            if not days or plan.observe_only:
                continue
            last = ctx.store.last_activity(plan.id)
            if last is None:
                continue  # no source observes this domain yet; silence is not drift
            idle_days = (ctx.now - last).days
            if idle_days < days:
                continue
            title = ctx.words("drift.idle", domain=plan.title, days=idle_days)
            first = next((g for g in sorted(plan.goals, key=lambda g: g.priority) if g.if_behind), None)
            out.append(
                Signal(
                    key=f"{plan.id}:idle",
                    domain=plan.id,
                    kind="idle",
                    priority=2,
                    level=idle_days - days + 1,
                    title=title,
                    actions=first.if_behind[:1] if first else (),
                    sensitivity=plan.sensitivity_default,
                    domain_priority=plan.priority,
                )
            )
        return out


def evaluate_all(rules: Sequence[DriftRule], ctx: EvalContext) -> list[Signal]:
    signals = [s for rule in rules for s in rule.evaluate(ctx)]
    return sorted(signals, key=lambda s: (s.domain_priority, s.priority, -s.level, s.key))
