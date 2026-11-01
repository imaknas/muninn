"""What a topic remembers, for a conversation that never ends.

Every line is kept as it was said (the thread's turns are never pruned); what is compressed is only what the
model reads in a round. A topic keeps one state (what was decided, what is still open, what comes next, and
which lines say so), refreshed from the previous state and the lines that have left the window since, never
a summary of a summary alone, so a detail it dropped can still be found in the lines it points to.

A round reads: the latest lines verbatim (the window, plus any that left it but are not in the state yet),
the topic's state, the user's own words from before the window verbatim, and the earlier lines that matter
to what they just said, picked once the sentence is known (topic_recall). A short conversation is read whole
and nothing is asked. The state is refreshed by the scheduled tick (topic_state), outside any request.

Both points are asked like every other, as the routing says; a lent session's lines may be read here, and the
flags they carry (local_only, via) go with whatever is made from them."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, Sequence

from .llm_router import LLMRequest, LLMRouter
from .models import Sensitivity
from .ports import Clock, StateStore, ThreadStore
from .language import Language
from .reply_json import json_object

WINDOW = 12  # the latest lines, always read as they are
HIS_WORDS = 6  # the user's own lines from before the window, read as they are too
FOLD_AT = 8  # lines out of the window and not yet in the state: the state is refreshed once there are this many
UNFOLDED_MAX = 40  # out of the window, not in the state yet: read as they are, at most this many
RECALL = 4  # earlier lines brought back for one sentence
CANDIDATES = 200  # earlier lines offered to pick from (the latest of them)
HEAD = 80  # how much of each offered line the picker sees
LINE_MAX = 1200  # a line brought back is cut here
ACTIVE = 50  # the latest conversations the refresh looks at
OTHERS = 8  # the other open topics named in a round

STATE_SYSTEM = """You keep the state of one topic of a long conversation between the user ("the user") and the user's assistant ("you").
Input is JSON: "previous" (the state so far, or null) and "lines" (lines that came after it, each with its number "i").
Write the state as it stands now, keeping everything in "previous" that is still true and adding what the lines say.
Reply with one JSON object and nothing else:
{"decided": ["<a decision or a fact the user gave, with its details: names, numbers, dates>"],
 "open": ["<what is still waiting, and on whom>"],
 "next": "<the next step, one sentence, or empty>",
 "where": [{"what": "<a subject>", "lines": [<the numbers of the lines that say it>]}]}
- Keep details exact (amounts, names, times). Never drop one that is still true because it seems small.
- "where" points to the lines, so the details can be read again: every decided and open item should have one."""

RECALL_SYSTEM = """You pick which earlier lines of a long conversation the assistant must read again to answer the user's
new sentence well. Input is JSON: "said" (the new sentence), "state" (the topic's state, pointing to line numbers, or
null) and "earlier" (earlier lines: number "i", who, and how each begins).
Reply with one JSON object and nothing else: {"lines": [<up to 4 numbers from earlier>], "why": "<a few words>"}
- Pick the lines whose details the answer depends on (what the user said of the thing the user is asking about, the numbers, the decision).
- Pick none when the new sentence needs nothing from before: {"lines": []}."""


class MemoryStorage(ThreadStore, StateStore, Protocol):
    """The conversations, and each topic's state (state "topic:memory:<thread>")."""


@dataclass(frozen=True)
class View:
    """What a round reads of its conversation."""

    recent: list[dict[str, Any]]  # the latest lines, oldest first
    earlier: list[dict[str, Any]] = field(default_factory=list)  # the user's own words and the lines brought back, each with "i"
    state: dict[str, Any] | None = None  # the topic's state: decided, open, next, where; with the flags of what it came from
    others: list[dict[str, Any]] = field(default_factory=list)  # the other open topics: title, state, cloud_ok

    def shown(self) -> list[dict[str, Any]]:
        """Every line the round reads, for its privacy flags."""
        return [*self.earlier, *self.recent]


def _key(tid: str) -> str:
    return f"topic:memory:{tid}"


def _who(x: dict[str, Any]) -> str:
    return "the user" if x.get("role") == "user" else "you"


def _strings(v: Any, n: int, size: int) -> list[str]:
    return [str(x)[:size] for x in v if str(x).strip()][:n] if isinstance(v, list) else []


def _numbers(v: Any, allowed: set[int], n: int) -> list[int]:
    out: list[int] = []
    for x in v if isinstance(v, list) else []:
        try:
            i = int(x)
        except (TypeError, ValueError):
            continue
        if i in allowed and i not in out:
            out.append(i)
    return out[:n]


def _flags(lines: Sequence[dict[str, Any]], before: dict[str, Any] | None) -> dict[str, Any]:
    """What a state made of these lines (and the previous state) carries: cleared only when every line of the user's
    was, local_only when any line was, and which lent sessions it came from."""
    before = before or {"cloud_ok": True, "local_only": False, "via": []}
    via = set(before.get("via") or [])
    for x in lines:
        v = x.get("via")
        via |= {v} if isinstance(v, str) else set(v or [])
    return {"cloud_ok": bool(before.get("cloud_ok")) and all(x.get("cloud_ok") for x in lines if x.get("role") == "user"),
            "local_only": bool(before.get("local_only")) or any(x.get("local_only") for x in lines),
            "via": sorted(via)}


class TopicMemory:
    def __init__(self, store: MemoryStorage, clock: Clock, router: LLMRouter,
                 open_topics: Callable[[], Sequence[dict[str, Any]]], *, words: Language):
        """`open_topics()`: the topics still open, as the panel lists them (thread, title, state)."""
        self.words = words
        self._store = store
        self._clock = clock
        self._router = router
        self._open = open_topics

    def memory(self, tid: str) -> dict[str, Any] | None:
        got = self._store.get_state(_key(tid))
        return got if isinstance(got, dict) else None

    # ── reading ─────────────────────────────────────────────────────────────

    def view(self, tid: str | None, turns: Sequence[dict[str, Any]], said: str) -> View:
        """What the round about `said` reads of conversation `tid`."""
        turns = [x for x in turns if x.get("role") in ("user", "muninn")]
        others = self._others(tid)
        out = len(turns) - WINDOW
        if out <= 0 or tid is None:
            return View(list(turns), others=others)
        mem = self.memory(tid) or {}
        upto = min(int(mem.get("upto") or 0), out)
        start = max(upto, out - UNFOLDED_MAX)  # what left the window but is not in the state yet is read as it is
        recent = list(turns[start:])
        before = list(enumerate(turns[:start]))
        his = [(i, x) for i, x in before if x.get("role") == "user"][-HIS_WORDS:]
        taken = {i for i, _ in his}
        state = mem.get("state")
        pool = [(i, x) for i, x in before if i not in taken][-CANDIDATES:]
        back = self._recall(said, state, pool) if pool else []
        earlier = [{**x, "i": i, "text": str(x.get("text") or "")[:LINE_MAX]}
                   for i, x in sorted([*his, *((i, turns[i]) for i in back)], key=lambda p: p[0])]
        return View(recent, earlier, {**state, **(mem.get("flags") or {})} if state else None, others)

    def _others(self, tid: str | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for x in self._open():
            if len(out) >= OTHERS:
                break
            if x.get("thread") == tid:
                continue
            t = self._store.thread(str(x["thread"])) or {}
            out.append({"title": x.get("title"), "state": x.get("state"),
                        "cloud_ok": all(y.get("cloud_ok") for y in t.get("turns") or [] if y.get("role") == "user")})
        return out

    def _recall(self, said: str, state: dict[str, Any] | None, pool: Sequence[tuple[int, dict[str, Any]]]) -> list[int]:
        """The earlier lines `said` depends on; none when nothing answered or the answer made no sense."""
        content = {"said": said, "state": state,
                   "earlier": [{"i": i, "who": _who(x), "begins": str(x.get("text") or "")[:HEAD]} for i, x in pool]}
        c = self._router.complete_case(LLMRequest(RECALL_SYSTEM, json.dumps(content, ensure_ascii=False),
                                                  Sensitivity.SENSITIVE, None, True, task="topic_recall"))
        data = json_object(c.reply)
        return _numbers(data.get("lines"), {i for i, _ in pool}, RECALL) if data else []

    # ── keeping the state ───────────────────────────────────────────────────

    def went_on(self, tid: str) -> None:
        """The conversation went on elsewhere (a deep talk came back): its state is folded again up to its last line
        at the next refresh, so what it says is open is not what was open before."""
        mem = self.memory(tid)
        if mem and mem.get("state"):
            self._store.set_state(_key(tid), {**mem, "refold": True})

    def refresh(self) -> int:
        """Folds into each topic's state the lines that left its window since, once there are enough of them
        (the scheduled tick); a conversation that went on elsewhere is folded up to its last line. How many states
        were refreshed; a topic whose model did not answer keeps its state and its lines stay read as they are."""
        refreshed = 0
        for tid, t in self._store.threads(ACTIVE):
            turns = [x for x in t.get("turns") or [] if x.get("role") in ("user", "muninn")]
            out = len(turns) - WINDOW
            mem = self.memory(tid) or {}
            upto = int(mem.get("upto") or 0)
            refold = bool(mem.get("refold"))
            if out - upto < FOLD_AT and not refold:
                continue
            lines = turns[upto:len(turns) if refold else out]
            content = {"previous": mem.get("state"),
                       "lines": [{"i": upto + k, "who": _who(x), "text": str(x.get("text") or "")} for k, x in enumerate(lines)]}
            c = self._router.complete_case(LLMRequest(STATE_SYSTEM, json.dumps(content, ensure_ascii=False),
                                                      Sensitivity.SENSITIVE, None, True, task="topic_state"))
            data = json_object(c.reply)
            if data is None:
                continue
            where = [{"what": str(w.get("what") or "")[:80], "lines": _numbers(w.get("lines"), set(range(out)), 12)}
                     for w in data.get("where") or [] if isinstance(w, dict)][:24]
            state = {"decided": _strings(data.get("decided"), 30, 300), "open": _strings(data.get("open"), 15, 300),
                     "next": str(data.get("next") or "")[:300], "where": where}
            self._store.set_state(_key(tid), {"state": state, "upto": max(upto, out), "flags": _flags(lines, mem.get("flags")),
                                              "ts": self._clock.now().isoformat()})
            refreshed += 1
        return refreshed
