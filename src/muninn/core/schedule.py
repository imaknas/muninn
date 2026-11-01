"""Put today's priorities into free time on the calendar.

Pure placement: given what to do (in order), how long each takes, the busy
intervals and the usable window, return where each block goes. The engine
writes the blocks into Muninn's own calendar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Sequence

from .language import Language
from .timewin import ActiveHours, local

DURATIONS = {  # minutes per kind of priority item
    "deadline_target": 45,
    "next_action": 30,
    "goal_action": 60,
    "deadline": 30,
    "packet": 15,
}
BUFFER = timedelta(minutes=10)
STEP = timedelta(minutes=15)


@dataclass(frozen=True)
class Task:
    title: str
    minutes: int
    note: str = ""


@dataclass(frozen=True)
class Block:
    task: Task
    start: datetime
    end: datetime


def _round_up(t: datetime) -> datetime:
    t = t.replace(second=0, microsecond=0)
    extra = t.minute % 15
    return t if extra == 0 else t + timedelta(minutes=15 - extra)


def place(tasks: Sequence[Task], busy: Sequence[tuple[datetime, datetime]], start: datetime, end: datetime,
          max_blocks: int = 3) -> list[Block]:
    """Earliest fit for each task in order, keeping BUFFER around busy time
    and around each placed block. Tasks that do not fit are skipped."""
    taken = sorted((a - BUFFER, b + BUFFER) for a, b in busy)
    out: list[Block] = []
    for task in tasks:
        if len(out) >= max_blocks:
            break
        length = timedelta(minutes=task.minutes)
        t = _round_up(start)
        while t + length <= end:
            clash = next(((a, b) for a, b in taken if a < t + length and t < b), None)
            if clash is None:
                out.append(Block(task, t, t + length))
                taken.append((t - BUFFER, t + length + BUFFER))
                taken.sort()
                break
            t = _round_up(max(clash[1], t + STEP))
    return sorted(out, key=lambda b: b.start)


def find_slot(calendar: Any, now: datetime, tz: str, hours: ActiveHours, title: str, day: date, start: str | None,
              minutes: int, earliest: str | None = None, *, words: Language) -> tuple[datetime, datetime]:
    """Where a block of `minutes` goes on `day`: at `start` (HH:MM) when given, else the first room
    in the calendar's busy times within the active hours, from `earliest` on. ValueError when none."""
    tzinfo = local(now, tz).tzinfo
    if start:
        s = datetime.combine(day, time.fromisoformat(start), tzinfo)
        return s, s + timedelta(minutes=minutes)
    lo, hi = max(now, datetime.combine(day, hours.start, tzinfo)), datetime.combine(day, hours.end, tzinfo)
    if earliest:
        lo = max(lo, datetime.combine(day, time.fromisoformat(earliest), tzinfo))
    blocks = place([Task(title, minutes)], calendar.busy(lo, hi), lo, hi, max_blocks=1)
    if not blocks:
        raise ValueError(words("schedule.no_room", day=day, minutes=minutes))
    return blocks[0].start, blocks[0].end
