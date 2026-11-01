"""Where the user is, from whichever tracking app they use. Muninn reads one thing: a location point (when, where, how
precise, from which device). Each app is one implementation of `LocationApp` (ports): it turns what the app posts into
points, answers the app as it expects, and says how to set the app up. The core never knows which app it was.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .models import Observation, Sensitivity

DEFAULT_ACCURACY_M = 50.0  # a point that says nothing of its precision is taken as this good


@dataclass(frozen=True)
class LocationPoint:
    ts: datetime  # when the device was there (timezone-aware)
    lat: float
    lon: float
    device: str  # which of the user's devices
    accuracy_m: float | None = None  # radius it may be off by, when the app says


@dataclass(frozen=True)
class AppSetup:
    """How the user sets an app up to send here."""

    link: str | None  # opens the app already configured, when the app takes such a link
    fields: list[tuple[str, str]] = field(default_factory=list)  # what to type in by hand, as (label, value)
    note: str = ""  # where in the app those go


def observation(p: LocationPoint, app: str) -> Observation:
    """A point as Muninn keeps it: sensitive, one per device and second (a point posted twice is kept once)."""
    payload = {"lat": p.lat, "lon": p.lon, "device": p.device, "app": app,
               **({"accuracy_m": p.accuracy_m} if p.accuracy_m is not None else {})}
    return Observation("location", p.ts, "location", f"location:{p.device}:{int(p.ts.timestamp())}", payload,
                       sensitivity=Sensitivity.SENSITIVE)


def accuracy_m(payload: dict) -> float:
    """How precise a kept point is (points kept before the contract named it carry the app's own `acc`)."""
    return float(payload.get("accuracy_m") or payload.get("acc") or DEFAULT_ACCURACY_M)
