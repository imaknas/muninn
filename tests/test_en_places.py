"""Where an English Muninn says the user was today (core/places.py)."""

from __future__ import annotations

import re

from conftest import at, make_harness
from test_places import track


def test_where_you_were_today_is_said_in_english():
    h = make_harness(at(2026, 10, 21, 22), lang="en")
    h.engine.ingest(track(), evaluate=False)
    assert h.engine.places_line().startswith("Where you were today: Elsewhere ")
    home = h.engine.home_view()["places"]
    h.engine.whereabouts.name("Office", home[1]["lat"], home[1]["lon"])
    h.engine.whereabouts.name("Home", home[0]["lat"], home[0]["lon"])
    line = h.engine.places_line()
    assert line == "Where you were today: Office 11.8h; Home 2.2h" and re.search(r"[一-鿿]", line) is None
