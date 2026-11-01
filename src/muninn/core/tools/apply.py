"""The tool for an application form Muninn is filling: the user's answer for one of its fields."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from . import Outcome, Turn
from .packets import packet_id

if TYPE_CHECKING:
    from ..apply import Applications


class AnswerForm:
    name: ClassVar[str] = "answer_form"
    description: ClassVar[str] = ("The user answers a question of an application form Muninn is filling, or changes what a field was filled with "
                                  "(the packet's topic lists the form's fields: asking, filled). args: {packet: <packet id; may be left out for the one under "
                                  "discussion>, field: <field id>, value: <the answer: for a field with options, one of them exactly; for several, a list>}")
    lookup: ClassVar[bool] = False

    def __init__(self, applications: Applications):
        self._applications = applications

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        return Outcome(self._applications.answer(packet_id(a, turn), str(a.get("field") or ""), a.get("value")))
