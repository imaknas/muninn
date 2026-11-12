"""The tools the command model may choose, one class each.

A tool has a name (what the model writes), the line describing it in the
model's tool list, whether it is a lookup (its result is the answer, and it
is never offered as a next step), and `run`, which checks the arguments and
does it. A lookup that asks a model of its own also has `prepare`, run while
the sentence is understood (the slow part, outside the engine's lock); what
it found reaches `run` as the turn's `answer`. Each tool takes only what it needs. The registry holds the tools in
order and is handed to the command runner; adding a tool is adding a class
and registering it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Collection, Protocol, Sequence

from ..language import Language
from ..models import Attachment
from ..privacy import Cleared


@dataclass
class Outcome:
    text: str
    action_id: int | None = None
    link: str | None = None
    confirm_id: int | None = None  # a question waiting for the user's yes/no
    handoff_id: int | None = None  # passed on to another project (can be cancelled)
    made: dict[str, Any] | None = None  # what it brought into being ({kind, id}), so what follows can be about it
    lend: str | None = None  # a project the conversation goes on with (lent once the round is done)
    failed: bool = False  # it was not done (the text says why)


@dataclass(frozen=True)
class LookedUp:
    """What a lookup's `prepare` found: handed to its `run` as the turn's answer (None: nothing answered)."""

    answer: Any
    stood_in: bool = False  # a local model answered in place of the cloud


@dataclass
class Turn:
    """What one round knows besides the arguments."""

    about: dict[str, Any] | None = None  # the item the user is discussing
    only_to: set[str] | None = None  # a lent session's content: only these projects may hear what it says
    via: tuple[str, ...] = ()  # where that content came from ("lease:<id>", "project:<name>"), with only_to
    answer: Any = None  # what the tool's `prepare` looked up before the round was carried out
    made: dict[str, Any] | None = None  # the latest thing this conversation brought into being ({kind, id})
    thread: str | None = None  # the conversation this round is in
    words: Language = field(kw_only=True)  # the language Muninn speaks to the user in


class Tool(Protocol):
    name: ClassVar[str]
    description: ClassVar[str]  # its line in the model's tool list
    lookup: ClassVar[bool]  # its result is the answer; never offered as a next step
    # optional, acts_on: ClassVar[tuple[str, ...]]: the kinds of things it settles ("idea", "conversation")
    # optional, prepare(args, said, cleared, attachments) -> LookedUp: a lookup's slow part, done while the sentence
    # is understood (`said`: the user's sentence; `cleared`: it may go to the cloud; `attachments`: files sent with it)

    def run(self, args: dict[str, Any], turn: Turn) -> Outcome:
        """Checks the arguments and does it; ValueError (said to the user) when it cannot."""
        ...


FACT = "record_fact"  # not run: the sentence goes to the fact analysis instead


def split_id(qid: Any, words: Language) -> tuple[str, str]:
    """A qualified id ("<domain>/<id>") as its two parts."""
    domain, _, rest = str(qid).partition("/")
    if not rest:
        raise ValueError(words("tools.unknown_id", qid=qid))
    return domain, rest


class ToolRegistry:
    def __init__(self, tools: Sequence[Tool]):
        self._tools = {t.name: t for t in tools}
        if len(self._tools) != len(tools):
            raise ValueError("two tools with the same name")

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    def acts_on(self, name: str) -> tuple[str, ...]:
        """The kinds of things a tool settles (its `acts_on`: "idea", or "conversation" for where the talk goes on)."""
        return tuple(getattr(self._tools.get(name), "acts_on", ()))

    @property
    def lookups(self) -> set[str]:
        return {n for n, t in self._tools.items() if t.lookup}

    def tool_list(self, without: Collection[str] = ()) -> str:
        """The tools as the model reads them, in registration order (`without`: these left out)."""
        return "\n".join(f"- {t.name}: {t.description}" for t in self._tools.values() if t.name not in without)

    def run(self, name: str, args: dict[str, Any], turn: Turn) -> Outcome:
        return self._tools[name].run(args, turn)

    def prepare(self, name: str, args: dict[str, Any], said: str, cleared: Cleared | None,
                attachments: Sequence[Attachment] = ()) -> LookedUp | None:
        """A lookup's slow part, for a tool that has one (None for one that does not)."""
        prepare = getattr(self._tools.get(name), "prepare", None)
        return prepare(args, said, cleared, attachments) if prepare is not None else None

    @classmethod
    def collect(cls, sources: Sequence[Any], order: Sequence[str]) -> ToolRegistry:
        """The tools the actors publish (each source's `tools()`), in `order` by name; tools the
        order does not name come last, as published."""
        tools = [t for s in sources for t in s.tools()]
        place = {name: i for i, name in enumerate(order)}
        return cls(sorted(tools, key=lambda t: place.get(t.name, len(place))))
