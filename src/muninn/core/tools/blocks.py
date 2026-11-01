"""Tools for the blocks Muninn puts on the user's calendar: schedule one, move it, cancel it."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, ClassVar

from ..actions import ActionGate, BlockMoved, BlockPut, BlockRemoved, DecidedBy
from ..ports import Calendar, Clock
from ..schedule import find_slot
from ..language import Language
from ..timewin import ActiveHours, local
from . import Outcome, Turn


class _Blocks:
    """What the three calendar tools share: finding room in a day and finding one of Muninn's blocks."""

    def __init__(self, calendar: Calendar | None, clock: Clock, tz: str, hours: ActiveHours, gate: ActionGate):
        """`hours`: the active hours a block may be placed in; the calendar is read here and
        written through the gate (the user's words: done, undoable)."""
        self._calendar = calendar
        self._clock = clock
        self._tz = tz
        self._hours = hours
        self._gate = gate

    def _cal(self, words: Language) -> Calendar:
        if self._calendar is None or not self._calendar.available():
            raise ValueError(words("tools_blocks.no_calendar"))
        return self._calendar

    def _place(self, words: Language, title: str, day: date, start: str | None, minutes: int,
               earliest: str | None = None) -> tuple[datetime, datetime]:
        return find_slot(self._cal(words), self._clock.now(), self._tz, self._hours, title, day, start, minutes, earliest, words=words)

    def _block(self, block_id: str, words: Language):
        cal = self._cal(words)
        now = self._clock.now()
        ev = next((x for x in cal.own_events(now - timedelta(days=1), now + timedelta(days=8)) if x.id == block_id), None)
        if ev is None:
            raise ValueError(words("tools_blocks.no_block"))
        return ev


class Schedule(_Blocks):
    name: ClassVar[str] = "schedule"
    description: ClassVar[str] = "Schedule a stretch of time on the calendar for something. args: {title: <short title>, date: <YYYY-MM-DD>, start: <HH:MM for a set time, or null to let Muninn find a free slot>, earliest: <HH:MM or null; \"afternoon\" 13:00, \"evening\" 19:00>, minutes: <integer>}"
    lookup: ClassVar[bool] = False

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        title = str(a["title"])[:60]
        day = date.fromisoformat(a["date"])
        minutes = max(10, min(int(a.get("minutes") or 60), 240))
        s, e = self._place(turn.words, title, day, a.get("start"), minutes, a.get("earliest"))
        ran = self._gate.run(BlockPut(title, s.isoformat(), e.isoformat()), DecidedBy.USER)
        return Outcome(ran.text, ran.id)


class MoveBlock(_Blocks):
    name: ClassVar[str] = "move_block"
    description: ClassVar[str] = "Move a slot Muninn scheduled to another time. args: {block: <block id>, date: <YYYY-MM-DD>, start: <HH:MM or null to find a free slot>, earliest: <HH:MM or null>}"
    lookup: ClassVar[bool] = False

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        ev = self._block(str(a["block"]), turn.words)
        date.fromisoformat(a["date"])  # a malformed date is refused before the calendar is touched
        minutes = int((ev.end - ev.start).total_seconds() // 60)
        ran = self._gate.run(BlockMoved(ev.id, ev.title, ev.start.isoformat(), ev.end.isoformat(), a["date"], a.get("start"),
                                        a.get("earliest"), minutes), DecidedBy.USER)
        return Outcome(ran.text, ran.id)


class CancelBlock(_Blocks):
    name: ClassVar[str] = "cancel_block"
    description: ClassVar[str] = "Cancel a slot Muninn scheduled. args: {block: <block id>}"
    lookup: ClassVar[bool] = False

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        ev = self._block(str(a["block"]), turn.words)
        ran = self._gate.run(BlockRemoved(ev.id, ev.title, ev.start.isoformat(), ev.end.isoformat()), DecidedBy.USER)
        return Outcome(ran.text, ran.id)


class BlockTools:
    """The calendar blocks' tools."""

    def __init__(self, **blocks: Any):
        """The same arguments every block tool takes (calendar, clock, tz, hours, gate)."""
        self._blocks = blocks

    def tools(self) -> list[Any]:
        return [Schedule(**self._blocks), MoveBlock(**self._blocks), CancelBlock(**self._blocks)]
