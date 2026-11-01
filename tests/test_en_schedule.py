"""What an English Muninn says when it cannot find room on the calendar (core/schedule.py), and how it names an
application's status (core/models.py), a domain it cannot find
(core/plans.py) and its push when it speaks up (core/speak_up.py)."""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

import pytest

from conftest import at, make_harness
from muninn.core.actions import DecidedBy, TrackerStatus
from muninn.core.models import status_labels
from muninn.core.schedule import find_slot
from muninn.core.timewin import ActiveHours
from muninn.languages import language

EN = language("en")
HAN = re.compile(r"[一-鿿]")


class FullDay:
    def busy(self, start, end):
        return [(start, end)]


def test_no_room_on_the_calendar_is_said_in_english():
    from datetime import time

    with pytest.raises(ValueError) as e:
        find_slot(FullDay(), at(2026, 10, 30, 9), "Asia/Taipei", ActiveHours(time(9), time(18)), "Write", date(2026, 10, 30), None, 60,
                  words=EN)
    assert str(e.value) == "No free 60 minutes on Oct 30"


def test_an_applications_statuses_are_named_in_english():
    labels = status_labels(EN)
    assert labels["todo"] == "to apply" and labels["withdrawn"] == "withdrawn" and all(HAN.search(v) is None for v in labels.values())
    h = make_harness(at(2026, 10, 21, 10), lang="en")
    assert h.engine.views().today()["status_labels"] == labels


def test_a_domain_that_is_not_there_is_said_in_english():
    h = make_harness(at(2026, 10, 21, 10), lang="en")
    with pytest.raises(ValueError, match="There is no domain called gardening"):
        h.engine.plans_actor.archive("gardening")


def test_speaking_up_pushes_in_english():
    h = make_harness(at(2026, 10, 25, 10), lang="en")
    h.engine.gate.run(TrackerStatus("jobs", "acme", "interview", "Acme sent an interview invitation"), DecidedBy.INFERRED_UNSURE)
    h.cloud.reply = h.local.reply = json.dumps({"actions": [], "reply": "OK"})
    c = h.engine.conversation()
    c.execute_turn(c.understand_turn(None, "Applications first today"))
    h.clock.t += timedelta(minutes=25)
    assert h.engine.speak_up().run()
    assert [m[0].title for m in h.channel.sent].count("Muninn has a question for you") == 1
