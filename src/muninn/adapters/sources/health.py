"""Steps and sleep from iOS Health, sent by a Shortcuts automation.

The automation POSTs one small JSON object, e.g. {"steps": 5321} during the
day and {"sleep": 431} in the morning (the summed durations of last night's
sleep samples). Shortcuts reports durations in different units depending on
how the action is set up, so a sleep value is read by size: above 1000 as
seconds, above 24 as minutes, otherwise hours.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from muninn.core.models import Observation, Sensitivity


def sleep_hours(value: float) -> float:
    if value > 1000:
        return value / 3600
    if value > 24:
        return value / 60
    return value


def parse_health(body: dict[str, Any], now: datetime, today: date) -> list[Observation]:
    """`today` is the user's local date: steps count toward it, and sleep
    reported on it is last night's."""
    out = []
    day = today.isoformat()
    if isinstance(body.get("date"), str):
        try:
            day = date.fromisoformat(body["date"]).isoformat()
        except ValueError:
            pass
    steps = _number(body.get("steps"))
    if steps is not None and steps >= 0:
        out.append(Observation("health", now, "steps", f"health:steps:{day}:{int(steps)}", {"steps": int(steps), "day": day},
                               domain="health", sensitivity=Sensitivity.PERSONAL))
    for key in ("sleep", "sleep_minutes", "sleep_hours"):
        value = _number(body.get(key))
        if value is not None and value > 0:
            hours = {"sleep_minutes": value / 60, "sleep_hours": value}.get(key, sleep_hours(value))
            if hours <= 20:  # a whole day asleep is a bad export, not a night
                out.append(Observation("health", now, "sleep", f"health:sleep:{day}:{round(hours, 2)}", {"hours": round(hours, 2), "day": day},
                                       domain="health", sensitivity=Sensitivity.PERSONAL))
            break
    return out


def _number(x: Any) -> float | None:
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x)
    if isinstance(x, str):  # Shortcuts sometimes sends numbers as text, with separators
        try:
            return float(x.replace(",", "").strip())
        except ValueError:
            return None
    return None
