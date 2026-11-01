"""Tools for where the user was: name a place."""

from __future__ import annotations

from datetime import time
from typing import TYPE_CHECKING, Any, ClassVar

from ..timewin import local
from . import Outcome, Turn

if TYPE_CHECKING:
    from ..places import Whereabouts


class NamePlace:
    name: ClassVar[str] = "name_place"
    description: ClassVar[str] = "The user says what a place the user was at today is called (e.g. \"I'm at the gym now\", \"this morning's place was school\"). args: {name: <the place's name>, at: <HH:MM of that time; may be left out for now or the latest stretch>}"
    lookup: ClassVar[bool] = False

    def __init__(self, whereabouts: Whereabouts, tz: str):
        self._where = whereabouts
        self._tz = tz

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        name = str(a.get("name") or "").strip()
        if not name:
            raise ValueError(turn.words("tools_places.which_name"))
        at = None
        if a.get("at"):
            try:
                hh, mm = str(a["at"]).split(":")[:2]
                at = time(int(hh), int(mm))
            except ValueError:
                raise ValueError(turn.words("tools_places.bad_time")) from None
        stay = self._where.stay_at(at)
        if stay is None:
            raise ValueError(turn.words("tools_places.nothing_then") if at else turn.words("tools_places.nothing_today"))
        r = self._where.name(name, stay.lat, stay.lon)
        if r is None:
            raise ValueError(turn.words("tools_places.not_kept"))
        span = f"{local(stay.start, self._tz):%H:%M}–{local(stay.end, self._tz):%H:%M}"
        return Outcome(turn.words("tools_places.named", done=r.text, span=span), action_id=r.id)
