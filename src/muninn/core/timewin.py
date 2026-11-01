"""Period windows and pro-rata progress, in the plan's local timezone."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ActiveHours:
    """Pro-rata targets accrue only during waking hours, so a weekly goal is not
    "behind" at 07:00 on Monday just because the calendar week has started."""

    start: time = time(8, 0)
    end: time = time(23, 0)

    def elapsed_in_day(self, local: datetime) -> float:
        day_start = datetime.combine(local.date(), self.start, local.tzinfo)
        day_end = datetime.combine(local.date(), self.end, local.tzinfo)
        total = (day_end - day_start).total_seconds()
        done = min(max((local - day_start).total_seconds(), 0.0), total)
        return done / total


def local(now: datetime, tz: str) -> datetime:
    return now.astimezone(ZoneInfo(tz))


def period_window(period: str, now: datetime, tz: str) -> tuple[datetime, datetime]:
    loc = local(now, tz)
    day_start = datetime.combine(loc.date(), time(0), loc.tzinfo)
    if period == "day":
        return day_start, day_start + timedelta(days=1)
    if period == "week":
        start = day_start - timedelta(days=loc.weekday())
        return start, start + timedelta(days=7)
    raise ValueError(f"unknown period {period!r}")


def iso_week_window(iso_week: str, tz: str) -> tuple[datetime, datetime]:
    """'2026-W42' → that week's local Monday 00:00 and the next Monday's."""
    m = re.fullmatch(r"(\d{4})-W(\d{2})", iso_week)
    if not m:
        raise ValueError(f"not an ISO week (YYYY-Www): {iso_week!r}")
    start = datetime.combine(date.fromisocalendar(int(m[1]), int(m[2]), 1), time(0), ZoneInfo(tz))
    return start, start + timedelta(days=7)


def iso_week(t: datetime) -> str:
    year, week, _ = t.isocalendar()
    return f"{year}-W{week:02d}"


def elapsed_fraction(period: str, now: datetime, tz: str, hours: ActiveHours = ActiveHours()) -> float:
    loc = local(now, tz)
    today = hours.elapsed_in_day(loc)
    if period == "day":
        return today
    if period == "week":
        return (loc.weekday() + today) / 7
    raise ValueError(f"unknown period {period!r}")


def local_date(now: datetime, tz: str) -> date:
    return local(now, tz).date()


def in_window(t: time, start: time, end: time) -> bool:
    """[start, end), allowing windows that wrap past midnight."""
    if start <= end:
        return start <= t < end
    return t >= start or t < end
