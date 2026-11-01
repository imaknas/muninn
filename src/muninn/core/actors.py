"""Muninn's core as actors: each capability (tasks, the plan review, questions,
packets, mail, ...) owns its records and reaches the rest only through what it
publishes. Two small faces, implemented by the actors that have them:

- ToolSource: the tools the conversation may use to have it do something;
- TopicSource: the kinds of item it can be discussed as, and what the model
  sees when one of them is the topic;
- JobSource: its periodic work, which the engine runs on each tick.

The composition root hands the actors to a ToolRegistry and a TopicRouter;
the conversation knows neither which actors there are nor how they keep things.
Actors do not import each other; what one needs from another is passed in.
Writes go through the ActionGate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from .tools import Tool


class ToolSource(Protocol):
    def tools(self) -> Sequence[Tool]:
        """What the conversation may have this actor do."""
        ...


class TopicSource(Protocol):
    kinds: tuple[str, ...]  # the kinds of item it answers for, e.g. ("task",)

    def topic(self, kind: str, ref: str) -> dict[str, Any] | None:
        """What the model sees while the user discusses this item (None when there is no such item)."""
        ...


@dataclass(frozen=True)
class Job:
    """Periodic work an actor publishes. `phase`: when in a tick it runs: "early" (before the
    attribution sweep), "late" (before the plans are evaluated) or "after" (acting on what the
    evaluation found). A failing job is logged and the tick goes on."""

    name: str
    run: Callable[[], Any]
    phase: str = "early"


class JobSource(Protocol):
    def jobs(self) -> Sequence[Job]: ...


class TopicRouter:
    """Finds the actor that answers for a kind of topic; one actor per kind."""

    def __init__(self, sources: Sequence[TopicSource]):
        self._by_kind: dict[str, TopicSource] = {}
        for s in sources:
            for k in s.kinds:
                if k in self._by_kind:
                    raise ValueError(f"two actors answer for topic {k!r}")
                self._by_kind[k] = s

    @property
    def kinds(self) -> set[str]:
        return set(self._by_kind)

    def topic(self, kind: str, ref: str) -> dict[str, Any] | None:
        source = self._by_kind.get(kind)
        return source.topic(kind, ref) if source else None
