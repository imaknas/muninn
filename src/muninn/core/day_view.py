"""What a wide screen shows beyond the phone's views: today as a timeline
(the calendar, what was observed being done, where the user was) and each
goal's value day by day through its week.

Activity is reported as domain and time only: window titles and repos stay
out, so the timeline shows no more than the progress numbers already do."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import datetime, time, timedelta
from typing import Any

from .metrics import PASSIVE_ACTIVITY_KINDS, Metric, span_of, stretches
from .models import DomainPlan
from .places import Stay
from .ports import Calendar, Clock, ObservationStore
from .progress import INCLUSIVE, measure_window
from .timewin import local, local_date, period_window
from .language import Language

log = logging.getLogger(__name__)


class DayView:
    def __init__(
        self,
        store: ObservationStore,
        clock: Clock,
        tz: str,
        plans: Callable[[], list[DomainPlan]],
        metrics: Mapping[str, Metric],
        stays: Callable[[datetime, datetime], list[Stay]],
        calendar: Calendar | None = None,
        max_gap: timedelta = timedelta(minutes=20),
        pad: timedelta = timedelta(minutes=10), *, words: Language,
    ):
        self.words = words
        self.store = store
        self.clock = clock
        self.tz = tz
        self.plans = plans
        self.metrics = metrics
        self.stays = stays
        self.calendar = calendar
        self.max_gap = max_gap  # the same joining as hours are estimated with
        self.pad = pad

    def today(self) -> dict[str, Any]:
        now = self.clock.now()
        start, end = period_window("day", now, self.tz)
        titles = {p.id: p.title for p in self.plans()}
        spans: dict[str, list[tuple[datetime, datetime]]] = {}
        for o in self.store.observations(start, now + INCLUSIVE, kinds=PASSIVE_ACTIVITY_KINDS):
            spans.setdefault(o.domain or "", []).append(span_of(o))
        activity = sorted(
            ({"start": self._iso(a), "end": self._iso(min(b + self.pad, now)), "domain": d or None,
              "title": titles.get(d, d) if d else self.words("day_view.unsorted")}
             for d, ss in spans.items() for a, b in stretches(ss, self.max_gap)),
            key=lambda x: x["start"])
        hours: dict[str, float] = {}
        for a in activity:
            h = (datetime.fromisoformat(a["end"]) - datetime.fromisoformat(a["start"])).total_seconds() / 3600
            hours[a["title"]] = round(hours.get(a["title"], 0) + h, 1)
        return {
            "now": self._iso(now),
            "events": self._events(start, end),
            "activity": activity,
            "hours": [{"title": t, "hours": h} for t, h in sorted(hours.items(), key=lambda x: -x[1]) if h >= 0.1],
            "stays": [{"start": self._iso(st.start), "end": self._iso(st.end), "place": st.place.name if st.place else None}
                      for st in self.stays(start, now)],
        }

    def _iso(self, t: datetime) -> str:
        return local(t, self.tz).isoformat()  # local clock times, as the timeline draws them

    def _events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """The user's events and Muninn's own blocks (`own`), soonest first;
        empty when the calendar can't be read."""
        if self.calendar is None:
            return []
        try:
            if not self.calendar.available():
                return []
            got = [(e, False) for e in self.calendar.events(start, end)] + [(e, True) for e in self.calendar.own_events(start, end)]
        except Exception as e:  # an unreachable calendar leaves the column empty, not the page broken
            log.warning("calendar read failed: %s", e)
            return []
        return [{"title": e.title.removeprefix("Muninn · "), "start": self._iso(e.start), "end": self._iso(e.end), "own": own}
                for e, own in sorted(got, key=lambda x: x[0].start)]

    def goal_days(self) -> dict[str, list[dict[str, Any]]]:
        """Per goal ("<domain>/<goal>"), its value on each day of this week,
        Monday first; days still to come have no value."""
        now = self.clock.now()
        week_start, _ = period_window("week", now, self.tz)
        today = local_date(now, self.tz)
        tzinfo = local(now, self.tz).tzinfo
        days = [local_date(week_start, self.tz) + timedelta(days=i) for i in range(7)]
        out: dict[str, list[dict[str, Any]]] = {}
        for plan in self.plans():
            for goal in plan.goals:
                if goal.metric not in self.metrics:
                    continue
                row = []
                for d in days:
                    if d > today:
                        row.append({"day": d.isoformat(), "value": None})
                        continue
                    a = datetime.combine(d, time(0), tzinfo)
                    b = min(a + timedelta(days=1), now + INCLUSIVE)
                    v = measure_window(plan, goal, a, b, self.store, self.metrics)
                    row.append({"day": d.isoformat(), "value": None if v is None else round(v, 2)})
                out[f"{plan.id}/{goal.id}"] = row
        return out
