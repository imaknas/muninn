"""Time away from the keyboard: the reading the computer cannot see.

Two ways in. An iPhone Focus (e.g. Reading) that the user turns on and off marks
a stretch exactly. A gap, a long stretch in waking hours with no activity at
all between two moments of activity, is asked about, at most a few times a
day and never when the calendar explains it. Either way the user says with
one tap which goal the time counts toward, or that it counts toward nothing,
and the time is kept as an `offline` observation, which the hours metric
counts like time in front of the computer.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Protocol, Sequence

from .metrics import OFFLINE_ACTIVITY, PASSIVE_ACTIVITY_KINDS, span_of
from .models import ActionRecord, Confirmation, Observation, Sensitivity
from .ports import ActionLog, Clock, ConfirmationStore, ObservationStore, StateStore
from .timewin import ActiveHours, local
from .language import Language

OFFLINE = OFFLINE_ACTIVITY
MIN_GAP = timedelta(minutes=45)
MIN_FOCUS = timedelta(minutes=10)
ASKS_PER_DAY = 2


class OfflineStorage(ObservationStore, ConfirmationStore, StateStore, ActionLog, Protocol):
    """The gaps observed, the questions asked about them, what was already asked, and the answers logged for undo."""


class OfflineTime:
    def __init__(self, store: OfflineStorage, clock: Clock, tz: str, hours: ActiveHours,
                 goals: Callable[[], Sequence[tuple[str, str]]],
                 busy: Callable[[datetime, datetime], Sequence[tuple[datetime, datetime]]] | None = None, *, words: Language):
        self.words = words
        self._store = store
        self._clock = clock
        self._tz = tz
        self._hours = hours
        self._goals = goals  # (title, "<domain>/<goal>") of the goals that count hours
        self._busy = busy

    # ── a Focus turned on and off on the phone ──────────────────────────────

    def focus(self, name: str, on: bool) -> Confirmation | None:
        now = self._clock.now()
        open_ = self._store.get_state("focus:open")
        if on:
            if not open_:
                self._store.set_state("focus:open", {"name": name, "start": now.isoformat()})
            return None
        if not open_:
            return None
        self._store.set_state("focus:open", None)
        start = datetime.fromisoformat(open_["start"])
        if now - start < MIN_FOCUS:
            return None
        minutes = (now - start).total_seconds() / 60
        said = self.words("offline_time.focus_said", name=open_["name"], duration=self.words("offline_time.duration", minutes=minutes))
        return self._ask(start, now, "focus", said, self.words("offline_time.focus_ask", said=said))

    # ── long stretches with no activity ─────────────────────────────────────

    def ask_gaps(self) -> int:
        now = self._clock.now()
        loc = local(now, self._tz)
        day_start = datetime.combine(loc.date(), self._hours.start, loc.tzinfo)
        day_end = datetime.combine(loc.date(), self._hours.end, loc.tzinfo)
        asked = self._store.get_state(f"offline:asked:{loc.date().isoformat()}") or []
        if len(asked) >= ASKS_PER_DAY:
            return 0
        spans = sorted(span_of(o) for o in self._store.observations(day_start - timedelta(hours=2), min(now, day_end))
                       if o.kind in (*PASSIVE_ACTIVITY_KINDS, OFFLINE))
        n = 0
        gaps, latest = [], None
        for s, e in spans:  # stretches between the latest end so far and the next start
            if latest is not None and s > latest:
                gaps.append((latest, s))
            latest = e if latest is None else max(latest, e)
        for prev_end, next_start in gaps:
            if prev_end < day_start:
                continue  # the night, or the morning before the day began
            start, end = prev_end, min(next_start, day_end)
            if end - start < MIN_GAP or start.isoformat() in asked or self._explained(start, end):
                continue
            minutes = (end - start).total_seconds() / 60
            a, b = local(start, self._tz), local(end, self._tz)
            said = self.words("offline_time.gap_said", start=f"{a:%H:%M}", end=f"{b:%H:%M}",
                              duration=self.words("offline_time.duration", minutes=minutes))
            if self._ask(start, end, "gap", said, self.words("offline_time.gap_ask", said=said)):
                asked.append(start.isoformat())
                n += 1
            if len(asked) >= ASKS_PER_DAY:
                break
        self._store.set_state(f"offline:asked:{loc.date().isoformat()}", asked)
        return n

    def _explained(self, start: datetime, end: datetime) -> bool:
        """A meeting or a block on the calendar covers most of it."""
        if self._busy is None:
            return False
        try:
            busy = self._busy(start, end)
        except Exception:  # calendar unreachable: ask rather than guess
            return False
        covered = sum((min(e, end) - max(s, start)).total_seconds() for s, e in busy if e > start and s < end)
        return covered >= 0.5 * (end - start).total_seconds()

    # ── asking, and the answer ──────────────────────────────────────────────

    def _ask(self, start: datetime, end: datetime, source: str, said: str, question: str) -> Confirmation | None:
        """`said`: the stretch in a few words (what the answer is logged as); `question`: the whole question."""
        goals = {title: qid for title, qid in self._goals()}
        return self._store.add_confirmation(Confirmation(
            None, self._clock.now(), "offline_time", question, (*goals, self.words("offline_time.not_counted")),
            {"start": start.isoformat(), "end": end.isoformat(), "source": source, "goals": goals, "said": said},
            key=f"offline:{start.isoformat()}"))

    def answer(self, data: dict[str, Any], answer: str) -> Observation | None:
        qid = (data.get("goals") or {}).get(answer)
        if not qid:
            return None
        start, end = datetime.fromisoformat(data["start"]), datetime.fromisoformat(data["end"])
        o = Observation("muninn", start, OFFLINE, f"offline:{data['start']}",
                        {"duration_s": (end - start).total_seconds(), "source": data.get("source"), "label": answer},
                        qid.split("/", 1)[0], qid, Sensitivity.PERSONAL)
        self._store.add_observations([o])
        return o

    def answered(self, c: Any, answer: str) -> None:
        """The user said which goal a stretch away from the keyboard counted toward (undoable)."""
        if self.answer(c.data, answer) is not None:
            # a question asked before it kept its stretch apart is Chinese: its stretch is what comes before the first comma
            said = c.data.get("said") or c.question.split(",")[0]
            self._store.log_action(ActionRecord(None, self._clock.now(), c.data["goals"][answer].split("/")[0],
                                                self.words("offline_time.logged", goal=answer, said=said), {"kind": "offline", "start": c.data["start"]}))

    def jobs(self) -> list[Any]:
        from .actors import Job

        return [Job("asking about offline time", self.ask_gaps)]
