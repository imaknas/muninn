"""The built-in schedule (config `jobs:`; the `schedule` command is something else, the day's calendar), for an installation no other scheduler runs: the server starts each
job at its time as its own `muninn` command, as cron or launchd would, so a long one (the nightly reflect) never
holds up the app, and two processes share the database as they always do.

    jobs: default                      # the times below
    jobs: {tick: 15m, morning: "08:30", evening: "22:30", reflect: "04:20",
               reflection: "mon 08:05", weekly: "sun 21:00"}   # any left out keeps its default; null turns one off

Unset (the default), nothing is started here: another scheduler runs the commands. When a job last ran is kept
in the store, so a restart does not run the morning report twice; a job at a time that fails is tried again on
the next rounds, three times a day at most.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

COMMANDS = {"tick": ["tick"], "morning": ["morning"], "evening": ["evening"], "weekly": ["weekly"],
            "reflect": ["reflect", "--budget", "90"], "reflection": ["reflection"]}
DEFAULT = {"tick": "15m", "morning": "08:30", "evening": "22:30", "reflect": "04:20", "reflection": "mon 08:05",
           "weekly": "sun 21:00"}
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ATTEMPTS = 3  # a job at a time, tried at most this often a day


@dataclass(frozen=True)
class When:
    every: timedelta | None = None  # an interval job
    at: time | None = None  # a job at a time of day
    days: tuple[int, ...] = ()  # weekdays (0 = Monday) for one at a time; empty: every day


def when(job: str, spec: Any) -> When:
    s = str(spec).strip().lower()
    if m := re.fullmatch(r"(\d+)\s*([mh])", s):
        return When(every=timedelta(minutes=int(m[1]) * (60 if m[2] == "h" else 1)))
    if m := re.fullmatch(r"(?:([a-z,]+)\s+)?(\d{1,2}):(\d{2})", s):
        days = tuple(DAYS.index(d) for d in m[1].split(",")) if m[1] else ()
        if m[1] and any(d not in DAYS for d in m[1].split(",")):
            raise ValueError(f"jobs.{job}: days are {', '.join(DAYS)}")
        return When(at=time(int(m[2]), int(m[3])), days=days)
    raise ValueError(f"jobs.{job}: {spec!r}; e.g. 15m, 1h, \"08:30\" or \"sun 21:00\"")


def plan(config: Any) -> dict[str, When]:
    """The jobs config turns on (empty: none, the default)."""
    if not config:
        return {}
    chosen = dict(DEFAULT) if config in ("default", True) else {**DEFAULT, **dict(config)}
    if unknown := set(chosen) - set(COMMANDS):
        raise ValueError(f"jobs: unknown job(s) {sorted(unknown)}; known: {', '.join(COMMANDS)}")
    return {job: when(job, spec) for job, spec in chosen.items() if spec}


class Schedule:
    def __init__(self, jobs: Mapping[str, When], tz: str, now: Callable[[], datetime],
                 run: Callable[[str, list[str]], int], state: Callable[[str], Any], save: Callable[[str, Any], None]):
        """`run(job, args)`: starts that muninn command and returns its exit code; `state`/`save`: where when each
        job last ran is kept."""
        self._jobs, self._tz = dict(jobs), ZoneInfo(tz)
        self._now, self._run, self._state, self._save = now, run, state, save
        self._running: set[str] = set()
        self._lock = threading.Lock()

    def due(self, job: str, now: datetime) -> bool:
        w, kept = self._jobs[job], self._state(f"schedule:{job}") or {}
        if w.every is not None:
            last = kept.get("started")
            return last is None or now - datetime.fromisoformat(last) >= w.every
        local = now.astimezone(self._tz)
        if w.days and local.weekday() not in w.days or local.time() < w.at:
            return False
        slot = local.date().isoformat()
        return kept.get("done") != slot and (kept.get("slot") != slot or kept.get("attempts", 0) < ATTEMPTS)

    def round(self) -> list[str]:
        """Starts every job due now that is not still running; returns their names."""
        now, started = self._now(), []
        for job in self._jobs:
            with self._lock:
                if job in self._running or not self.due(job, now):
                    continue
                self._running.add(job)
            slot = now.astimezone(self._tz).date().isoformat()
            kept = self._state(f"schedule:{job}") or {}
            attempts = kept.get("attempts", 0) + 1 if kept.get("slot") == slot else 1
            self._save(f"schedule:{job}", {**kept, "started": now.isoformat(), "slot": slot, "attempts": attempts})
            threading.Thread(target=self._go, args=(job, slot), daemon=True, name=f"schedule-{job}").start()
            started.append(job)
        return started

    def _go(self, job: str, slot: str) -> None:
        try:
            code = self._run(job, COMMANDS[job])
            if code == 0:
                self._save(f"schedule:{job}", {**(self._state(f"schedule:{job}") or {}), "done": slot})
            else:
                log.warning("scheduled %s exited %s", job, code)
        except Exception:  # noqa: BLE001 — one job failing must not stop the schedule
            log.exception("scheduled %s failed", job)
        finally:
            with self._lock:
                self._running.discard(job)

    def start(self, every_s: float = 30.0) -> threading.Thread:
        stop = threading.Event()

        def loop() -> None:
            while not stop.wait(every_s):
                try:
                    self.round()
                except Exception:  # noqa: BLE001
                    log.exception("schedule round failed")

        t = threading.Thread(target=loop, daemon=True, name="schedule")
        t.start()
        log.info("built-in schedule: %s", ", ".join(self._jobs))
        return t
