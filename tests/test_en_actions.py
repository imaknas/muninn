"""In English, what Muninn did says so in English, and so do the questions it asks first and the plan changes it
names (core/actions.py, core/plan_edits.py). Invented domains only."""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from muninn.adapters.plans_yaml import YamlPlanRepo
from muninn.core.actions import DecidedBy
from muninn.core.plan_edits import RemoveEntry, SetGoalTarget
from muninn.languages import language

from conftest import TZ, at, make_harness

DRAFT = {
    "domain": {"id": "pottery", "title": "Pottery", "priority": 3},
    "timezone": TZ,
    "goals": [{"id": "wheel", "title": "Wheel practice", "metric": "hours", "target": 3, "period": "week"}],
    "deadlines": [{"id": "kiln", "title": "Book a kiln slot", "due": "2026-11-20"}],
}


def english(text: str) -> bool:
    return re.search(r"[一-鿿]", text) is None


def test_a_domain_made_and_put_away_says_so_in_english():
    h = make_harness(at(2026, 10, 24, 10), lang="en")
    ran = h.engine.plans_actor.create(DRAFT, DecidedBy.USER)
    assert ran.text == 'Create the domain "Pottery"'
    gone = h.engine.plans_actor.archive("pottery")
    assert gone.text == 'Archive the domain "Pottery" (its data stays)' and english(gone.text)


def test_what_is_asked_first_is_asked_in_english_and_its_yes_does_it():
    h = make_harness(at(2026, 10, 24, 10), lang="en")
    ran = h.engine.plans_actor.create(DRAFT, DecidedBy.INFERRED_UNSURE)
    [c] = [c for c in h.engine.pending_confirmations() if c.id == ran.confirm_id]
    assert (c.question, tuple(c.options)) == ('Create the domain "Pottery"?', ("Yes", "No"))
    h.engine.answer(c.id, "Yes")
    assert h.engine._plan("pottery", include_unconfirmed=True).title == "Pottery"


def test_an_idea_kept_and_settled_says_so_in_english():
    h = make_harness(at(2026, 10, 24, 10), lang="en")
    ran, n = h.engine.ideas.capture("A rain gauge from a bottle")
    assert ran.text.startswith("Idea kept: A rain gauge from a bottle (called ") and english(ran.text)
    assert h.engine.ideas.settle(n, "shelved").text == 'Idea "A rain gauge from a bottle" shelved'


def test_a_plan_change_names_itself_and_one_that_does_not_fit_is_refused_in_english(tmp_path):
    h = make_harness(at(2026, 10, 24, 10), plans=[], lang="en")
    h.engine.plans = YamlPlanRepo(tmp_path, TZ)
    h.engine.plans_actor.create(DRAFT, DecidedBy.USER)
    ran = h.engine.apply_plan_edit("pottery", SetGoalTarget("wheel", 4))
    assert ran.text == 'Change "Wheel practice" to 4 a week (now 3)'
    ran = h.engine.apply_plan_edit("pottery", RemoveEntry("deadlines", "kiln"))
    assert ran.text == 'Take "Book a kiln slot" out of the plan'
    with pytest.raises(ValueError, match="^There is no goal glaze$"):
        h.engine.apply_plan_edit("pottery", SetGoalTarget("glaze", 1))


def test_a_time_on_the_calendar_is_written_the_english_way():
    tz = ZoneInfo(TZ)
    start, end = datetime(2026, 10, 30, 14, tzinfo=tz), datetime(2026, 10, 30, 15, 30, tzinfo=tz)
    assert language("en")("actions.when", day=start, start=start, end=end) == "Oct 30, 14:00–15:30"
    assert language("zh-TW")("actions.when", day=start, start=start, end=None) == "10/30 14:00"
