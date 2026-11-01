"""Where the user was: named places and stays.

A place is a named circle (center, radius) the user labels in the app, with
an optional domain (work, school…). A stay is a stretch of location points
that keep within a small radius for a while. Location is sensitive: it stays
on the machine and only the local model may ever read it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Iterable, Protocol, Sequence

from .actions import ActionGate, DecidedBy, PlaceNamed, Ran
from .location import DEFAULT_ACCURACY_M, accuracy_m
from .metrics import PASSIVE_ACTIVITY_KINDS, estimate_hours, span_of
from .models import Observation
from .ports import Clock, ObservationStore, StateStore
from .progress import INCLUSIVE
from .timewin import local, period_window
from .language import Language

STAY_RADIUS_M = 150
MIN_STAY = timedelta(minutes=10)
MAX_SLACK_M = 1500  # how far a coarse fix's error may stretch a match
JOIN_SLACK_M = 500  # how far a coarse fix may sit from a stay and still belong to it


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    radius_m: float = 150
    domain: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Place:
        return cls(str(d["name"]), float(d["lat"]), float(d["lon"]), float(d.get("radius_m", 150)), d.get("domain"))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "lat": self.lat, "lon": self.lon, "radius_m": self.radius_m, "domain": self.domain}


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def place_at(lat: float, lon: float, places: Iterable[Place], acc: float = 0) -> Place | None:
    """The named place this position is in. A coarse fix (large `acc`, e.g.
    from cell towers) still counts when the place lies within its error."""
    slack = min(max(acc, 0), MAX_SLACK_M)
    best = None
    for p in places:
        d = distance_m(lat, lon, p.lat, p.lon)
        if d <= p.radius_m + slack and (best is None or d - p.radius_m < best[0]):
            best = (d - p.radius_m, p)
    return best[1] if best else None


def _acc(o: Observation) -> float:
    try:
        return accuracy_m(o.payload)
    except (TypeError, ValueError):
        return DEFAULT_ACCURACY_M


@dataclass(frozen=True)
class Stay:
    start: datetime
    end: datetime
    lat: float
    lon: float
    place: Place | None

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


def stays(points: Sequence[Observation], places: Sequence[Place], until: datetime | None = None) -> list[Stay]:
    """Groups time-ordered location points into stays.

    Phones in low-power mode report only when they move, so silence means
    "still here": a stay lasts until the first point outside its radius (that
    point's time ends it), and the latest stay lasts until `until` (now)."""
    pts = sorted((o for o in points if "lat" in o.payload and "lon" in o.payload), key=lambda o: o.ts)
    groups: list[list[Observation]] = []
    for o in pts:
        if groups:
            anchor = min(groups[-1], key=_acc)  # the group's most accurate fix
            d = distance_m(anchor.payload["lat"], anchor.payload["lon"], o.payload["lat"], o.payload["lon"])
            if d <= STAY_RADIUS_M + min(_acc(o), JOIN_SLACK_M):
                groups[-1].append(o)
                continue
        groups.append([o])
    raw: list[Stay] = []
    for i, g in enumerate(groups):
        start = g[0].ts
        if i + 1 < len(groups):
            end = groups[i + 1][0].ts
        else:
            end = max(until, g[-1].ts) if until else g[-1].ts
        current = until is not None and i == len(groups) - 1
        # weight by accuracy: one precise fix outweighs many coarse ones
        w = [1 / max(_acc(o), 5) ** 2 for o in g]
        lat = sum(o.payload["lat"] * x for o, x in zip(g, w)) / sum(w)
        lon = sum(o.payload["lon"] * x for o, x in zip(g, w)) / sum(w)
        best_acc = min(_acc(o) for o in g)
        raw.append((Stay(start, end, lat, lon, place_at(lat, lon, places, best_acc)), current))
    out: list[Stay] = []
    for st, current in raw:
        if out and st.place is not None and out[-1].place == st.place:
            prev = out.pop()  # same named place on both sides of a jitter: one stay
            st = Stay(prev.start, st.end, prev.lat, prev.lon, st.place)
        elif st.end - st.start < MIN_STAY and not current:
            continue  # passing through (where the user is now always shows)
        out.append(st)
    return out


def merge_places(*lists: Iterable[dict[str, Any]]) -> list[Place]:
    """Later lists win for the same name (the app's names over config)."""
    by_name: dict[str, Place] = {}
    for lst in lists:
        for d in lst or ():
            p = Place.from_dict(d)
            by_name[p.name] = p
    return list(by_name.values())


class PlaceStorage(ObservationStore, StateStore, Protocol):
    """The location points, and the places named in the app (state "places")."""


class Whereabouts:
    """The named places and where the user stayed. Location stays on the machine."""

    def __init__(self, store: PlaceStorage, clock: Clock, tz: str, configured: Callable[[], Sequence[dict[str, Any]]],
                 domains: Callable[[], Sequence[tuple[str, str]]], on_named: Callable[[], Any], gate: ActionGate, *, words: Language):
        """`configured`: the places from attribution.yaml; `domains`: (id, title) of every
        domain; `on_named`: called after a place is named (its points may now be attributed);
        `gate`: what the user names goes through it, and is undone through it."""
        self.words = words
        self._store = store
        self._clock = clock
        self._tz = tz
        self._configured = configured
        self._domains = domains
        self._on_named = on_named
        self._gate = gate

    def places(self) -> list[Place]:
        """Places from attribution.yaml, then those named in the app."""
        return merge_places(self._configured(), self._store.get_state("places") or [])

    def name(self, name: str, lat: float, lon: float) -> Ran | None:
        """The user names where they were (a button or their words): through the gate, undoable."""
        return self._gate.run(PlaceNamed(name, float(lat), float(lon)), DecidedBy.USER)

    def saved(self, name: str) -> dict[str, Any] | None:
        """The place named so in the app, as stored (what naming it again would replace)."""
        return next((p for p in (self._store.get_state("places") or []) if p["name"] == name.strip()[:40]), None)

    def saved_names(self) -> list[str]:
        """The places named in the app (those in attribution.yaml are the file's to change)."""
        return [p["name"] for p in self._store.get_state("places") or []]

    def stay_at(self, at: time | None) -> Stay | None:
        """Today's stay at that time of day, or the latest one (where the user is now)."""
        today = self.day_stays()
        if at is None:
            return today[-1] if today else None
        return next((st for st in today if local(st.start, self._tz).time() <= at <= local(st.end, self._tz).time()), None)

    def tools(self) -> list[Any]:
        from .tools.places import NamePlace

        return [NamePlace(self, self._tz)]

    def write_place(self, name: str, lat: float, lon: float, domain: str | None, radius_m: float = 150) -> Place:
        """The write itself, as the gate's PlaceNamed action carries it out; naming a place goes through name()."""
        name = name.strip()[:40]
        if not name:
            raise ValueError("place needs a name")
        if domain is not None and domain not in {d for d, _ in self._domains()}:
            raise ValueError(f"unknown domain {domain}")
        saved = [p for p in (self._store.get_state("places") or []) if p["name"] != name]
        place = Place(name, float(lat), float(lon), float(radius_m), domain)
        self._store.set_state("places", saved + [place.to_dict()])
        self._on_named()
        return place

    def put_back(self, saved: dict[str, Any]) -> None:
        """A place as it was stored before (an undo), even one whose domain was put away since."""
        others = [p for p in (self._store.get_state("places") or []) if p["name"] != saved["name"]]
        self._store.set_state("places", others + [saved])
        self._on_named()

    def stays_between(self, start: datetime, until: datetime) -> list[Stay]:
        # the last point before the window says where it started
        earlier = self._store.observations(start - timedelta(days=2), start, kinds=("location",))
        points = earlier[-1:] + self._store.observations(start, until + INCLUSIVE, kinds=("location",))
        got = stays(points, self.places(), until=until)
        return [Stay(max(st.start, start), min(st.end, until), st.lat, st.lon, st.place) for st in got if st.end > start]

    def _day(self, day: date | None) -> tuple[datetime, datetime]:
        now = self._clock.now()
        start, end = period_window("day", now if day is None else datetime.combine(day, time(12), local(now, self._tz).tzinfo), self._tz)
        return start, min(end, now)

    def day_stays(self, day: date | None = None) -> list[Stay]:
        return self.stays_between(*self._day(day))

    def where_and_what(self, start: datetime, until: datetime) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
        """Hours at each place, and per place the hours of observed activity
        in each domain while there. A place is context, not a domain."""
        place_hours: dict[str, float] = {}
        by_place: dict[str, dict[str, float]] = {}
        acts = self._store.observations(start, until + INCLUSIVE, kinds=PASSIVE_ACTIVITY_KINDS)
        for st in self.stays_between(start, until):
            name = st.place.name if st.place else self.words("places.elsewhere")
            place_hours[name] = place_hours.get(name, 0) + st.hours
            spans: dict[str, list] = {}
            for o in acts:
                a, b = span_of(o)
                if o.domain and a < st.end and b >= st.start:
                    spans.setdefault(o.domain, []).append((max(a, st.start), min(b, st.end)))
            for domain, sp in spans.items():
                h = estimate_hours(sp, timedelta(minutes=20), timedelta(minutes=10))
                by_place.setdefault(name, {})[domain] = by_place.get(name, {}).get(domain, 0) + h
        return place_hours, by_place

    def places_line(self, day: date | None = None) -> str | None:
        hours, by_place = self.where_and_what(*self._day(day))
        if not hours:
            return None
        titles = dict(self._domains())
        parts = []
        for name, h in sorted(hours.items(), key=lambda kv: -kv[1]):
            doing = sorted(by_place.get(name, {}).items(), key=lambda kv: -kv[1])
            detail = self.words.join(f"{titles.get(d, d)} {v:.1f}h" for d, v in doing if v >= 0.1)
            parts.append(self.words("places.place_hours", name=name, hours=f"{h:.1f}", detail=detail))
        return self.words("places.today", places=self.words("places.place_separator").join(parts))
