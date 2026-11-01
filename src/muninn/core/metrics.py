"""How a goal's `metric` is measured. Each metric name maps to one implementation;
the engine receives the mapping, so a new metric is a new class, not a branch.

A metric also says how its goal is judged:
- direction "at_least" (reach the target) or "at_most" (stay under it);
- accrues: True if the value builds up over the period, so progress is judged
  pro-rata; False if it is a single reading (e.g. last night's sleep).
`measure` returns None when there is no data to judge by.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Protocol

from .models import DomainPlan, Goal, Observation
from .ports import ObservationStore
from .timewin import local

HOURS_CORRECTION = "hours_correction"
# Activity the user did not report but was observed doing.
PASSIVE_ACTIVITY_KINDS = ("commit", "claude_activity", "window")
OFFLINE_ACTIVITY = "offline"  # time away from the keyboard the user said counts for a goal (offline_time.py)


class Metric(Protocol):
    unit: str
    direction: str  # at_least | at_most
    accrues: bool

    def measure(self, plan: DomainPlan, goal: Goal, start: datetime, end: datetime, store: ObservationStore) -> float | None: ...


@dataclass(frozen=True)
class TrackerDateCount:
    """Counts tracker rows whose date field (e.g. `applied`) falls in the window."""

    field: str = "applied"
    unit: str = "count"
    direction: str = "at_least"
    accrues: bool = True

    def measure(self, plan, goal, start, end, store) -> float | None:
        tz = local(start, plan.timezone).tzinfo
        n = 0
        for item in plan.tracker:
            d = getattr(item, self.field, None)
            if d is not None and start <= datetime.combine(d, time(0), tz) < end:
                n += 1
        return float(n)


def span_of(o: Observation) -> tuple[datetime, datetime]:
    """An observation covers its moment, or [ts, ts + duration_s] when the
    source measured how long it lasted (e.g. a window kept in focus)."""
    return o.ts, o.ts + timedelta(seconds=float(o.payload.get("duration_s", 0) or 0))


def stretches(spans: list[tuple[datetime, datetime]] | list[datetime], max_gap: timedelta) -> list[tuple[datetime, datetime]]:
    """Activity spans joined where they are separated by at most `max_gap`,
    in time order. Plain timestamps count as zero-length spans."""
    norm = sorted((s, s) if isinstance(s, datetime) else s for s in spans)
    out: list[tuple[datetime, datetime]] = []
    for start, end in norm:
        if out and start - out[-1][1] <= max_gap:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def estimate_hours(spans: list[tuple[datetime, datetime]] | list[datetime], max_gap: timedelta, pad: timedelta) -> float:
    """Each stretch of joined activity counts its length plus `pad` (the work
    before the first and after the last signal)."""
    return sum(((b - a) + pad for a, b in stretches(spans, max_gap)), timedelta()).total_seconds() / 3600


@dataclass(frozen=True)
class ActivityHours:
    """Hours estimated from activity observations tagged with the goal, plus
    the user's manual corrections (signed deltas)."""

    max_gap: timedelta = timedelta(minutes=20)
    pad: timedelta = timedelta(minutes=10)
    unit: str = "h"
    direction: str = "at_least"
    accrues: bool = True

    def measure(self, plan, goal, start, end, store) -> float | None:
        qid = f"{plan.id}/{goal.id}"
        obs = store.observations(start, end, goal=qid)
        spans = [span_of(o) for o in obs if o.kind in PASSIVE_ACTIVITY_KINDS or o.kind == OFFLINE_ACTIVITY]
        corrections = sum(float(o.payload.get("hours", 0)) for o in obs if o.kind == HOURS_CORRECTION)
        return max(0.0, estimate_hours(spans, self.max_gap, self.pad) + corrections)


def night_window(at: datetime, tz: str, cutoff: time, morning: time) -> tuple[datetime, datetime]:
    """The night `at` belongs to: from `cutoff` on day X to `morning` on X+1,
    where X is the calendar day of (at - morning). Before the cutoff, that is
    tonight (not started yet, so it measures zero)."""
    loc = local(at, tz)
    shift = timedelta(hours=morning.hour, minutes=morning.minute)
    x = (loc - shift).date()
    start = datetime.combine(x, cutoff, loc.tzinfo)
    end = datetime.combine(x + timedelta(days=1), morning, loc.tzinfo)
    return start, end


@dataclass(frozen=True)
class LateActivityMinutes:
    """Minutes of observed activity (any domain) after the goal's `cutoff`
    (default 23:30) and before 06:00."""

    max_gap: timedelta = timedelta(minutes=20)
    pad: timedelta = timedelta(minutes=10)
    morning: time = time(6, 0)
    unit: str = "min"
    direction: str = "at_most"
    accrues: bool = False

    def measure(self, plan, goal, start, end, store) -> float | None:
        cutoff = time.fromisoformat(str(goal.params.get("cutoff", "23:30")))
        n_start, n_end = night_window(end, plan.timezone, cutoff, self.morning)
        if end <= n_start:
            return 0.0
        stop = min(end, n_end)
        obs = store.observations(n_start, stop, kinds=PASSIVE_ACTIVITY_KINDS)
        spans = [(a, min(b, stop)) for a, b in map(span_of, obs)]
        return estimate_hours(spans, self.max_gap, self.pad) * 60 if obs else 0.0


@dataclass(frozen=True)
class SleepFromActivityGap:
    """Last night's sleep, estimated as the longest gap between observed
    activity from 18:00 the evening before to 14:00 today. A stand-in until a
    real sleep source exists; None until there is activity on both sides."""

    evening: time = time(18, 0)
    noon: time = time(14, 0)
    unit: str = "h"
    direction: str = "at_least"
    accrues: bool = False

    def measure(self, plan, goal, start, end, store) -> float | None:
        loc = local(end, plan.timezone)
        lo = datetime.combine(loc.date() - timedelta(days=1), self.evening, loc.tzinfo)
        hi = min(end, datetime.combine(loc.date(), self.noon, loc.tzinfo))
        spans = sorted(span_of(o) for o in store.observations(lo, hi, kinds=PASSIVE_ACTIVITY_KINDS))
        if len(spans) < 2:
            return None
        # longest stretch between the end of one activity and the start of the next
        gap, woke = max((nxt[0] - prev[1], nxt[0]) for prev, nxt in zip(spans, spans[1:]))
        if local(woke, plan.timezone).date() != loc.date():
            return None  # longest gap is still last night's evening: not up yet
        return gap.total_seconds() / 3600


@dataclass(frozen=True)
class ReportedDailyMax:
    """Largest value of payload[field] reported today by observations of
    `kind` (e.g. cumulative step counts pushed from the phone)."""

    kind: str = "steps"
    field: str = "steps"
    unit: str = "steps"
    direction: str = "at_least"
    accrues: bool = True

    def measure(self, plan, goal, start, end, store) -> float | None:
        values = [float(o.payload.get(self.field, 0)) for o in store.observations(start, end, kinds=(self.kind,))]
        return max(values) if values else None


@dataclass(frozen=True)
class ReportedSleep:
    """Last night's sleep as the phone measured it: the latest `sleep`
    observation for today's date (a later report corrects an earlier one)."""

    unit: str = "h"
    direction: str = "at_least"
    accrues: bool = False

    def measure(self, plan, goal, start, end, store) -> float | None:
        day = local(end, plan.timezone).date().isoformat()
        reports = [o for o in store.observations(end - timedelta(days=2), end, kinds=("sleep",)) if o.payload.get("day") == day]
        return float(max(reports, key=lambda o: o.ts).payload["hours"]) if reports else None


@dataclass(frozen=True)
class FirstOf:
    """The first metric that has a value: a real measurement before an estimate."""

    metrics: tuple[Metric, ...]

    @property
    def unit(self) -> str:
        return self.metrics[0].unit

    @property
    def direction(self) -> str:
        return self.metrics[0].direction

    @property
    def accrues(self) -> bool:
        return self.metrics[0].accrues

    def measure(self, plan, goal, start, end, store) -> float | None:
        for m in self.metrics:
            if (v := m.measure(plan, goal, start, end, store)) is not None:
                return v
        return None


def default_metrics() -> dict[str, Metric]:
    return {
        "applications_submitted": TrackerDateCount("applied"),
        "hours": ActivityHours(),
        "late_activity": LateActivityMinutes(),
        "sleep_hours": FirstOf((ReportedSleep(), SleepFromActivityGap())),
        "steps": ReportedDailyMax(),
    }
