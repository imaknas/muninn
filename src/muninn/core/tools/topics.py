"""Tools for the topics of the conversation: say one is done, to set one aside, or to take it back up."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from . import Outcome, Turn

if TYPE_CHECKING:
    from ..topics import Topics


class SettleTopic:
    name: ClassVar[str] = "settle_topic"
    description: ClassVar[str] = ("The user says another matter being talked about is done, to set it aside for now, or to take a shelved one back up (e.g. \"the dentist thing is done\", \"forget about the phone upgrade for now\"). "
                                  "args: {topic: <that matter, in the user's words>, decision: <close | shelve | reopen>}")
    lookup: ClassVar[bool] = False
    SAID: ClassVar[dict[str, str]] = {"closed": "tools_topics.closed", "shelved": "tools_topics.shelved", "open": "tools_topics.reopened"}

    def __init__(self, topics: Topics):
        self._topics = topics

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        said = str(a.get("topic") or "").strip()
        if not said:
            raise ValueError(turn.words("tools_topics.which"))
        r = self._topics.settle(said, str(a.get("decision") or ""))
        return Outcome(turn.words(self.SAID[r["state"]], title=r["title"]))
