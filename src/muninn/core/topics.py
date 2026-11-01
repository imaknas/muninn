"""The topics of the one conversation stream. Each conversation is a topic: what it is about, and where it
stands — waiting on the user, being worked on, closed, or set aside (shelved).

Which topic a sentence belongs to: it goes on with the conversation on screen, goes back to another one
still going (a dentist question in the middle of an experiment), or starts a new one. Asked like every
other question, as the routing says (everything to the cloud, or sensitive things to the local model).
Nothing is asked when there is nothing to choose between: no other conversation still going, the user
just opened one (from the panel), or the sentence names an item by its number.

Where a topic stands comes from the round itself (the routing reply's `topic`), from what the user says
of it (settle), and from time: one nobody touched for a week is set aside."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Sequence

from .decide import Decider, Question
from .models import Sensitivity
from .ports import Clock, ThreadStore
from .tools.topics import SettleTopic
from .language import Language

GOING = timedelta(days=3)  # a conversation talked in within this long is still going
CANDIDATES = 8  # at most this many other conversations are offered
QUIET = timedelta(days=7)  # an open topic untouched this long is set aside
OPEN = ("waiting", "doing", "open")
STATES = ("waiting", "doing", "open", "shelved", "closed")

SYSTEM = """You decide which conversation the user's new sentence belongs to.
Input is JSON: "said" (the new sentence), "current" (the conversation on screen: its title and its last lines), and
"others" (other conversations still going: id, title, last line).
Reply with one JSON object and nothing else: {"topic": "current" | "<an id from others>" | "new", "why": "<a few words>"}
- current: it goes on with the conversation on screen (an answer, a follow-up, anything that only makes sense there).
- an id from others: it is plainly about that other conversation (names its subject, or answers what it last asked).
- new: it starts something none of them is about.
- When unsure, current."""

# a conversation lent to a project's session: where the sentence goes and what in it is for Muninn, in one judgment
LENT = """You decide where the user's new sentence goes. The conversation on screen is lent to the project "%s": that
project's session answers what the user says there, and Muninn listens beside it.
Input is JSON: "said" (the new sentence), "current" (that talk: its title and its latest lines, the user's, the project
session's and Muninn's, there so you can tell what the user's words refer to, e.g. "this question" is what the session just said), and
"others" (other conversations still going: id, title, last line).
Reply with one JSON object and nothing else:
{"topic": "current" | "<an id from others>" | "new", "for_muninn": "<one sentence>" | null, "end": true | false, "why": "<a few words>"}
- current: it goes on with the talk with the project (anything about that project, the talk, files or results to look at).
  for_muninn: the part of it meant for Muninn rather than the project (a reminder, something to keep, a task or a fact
  about another part of the user's life, a question about the user's own plans or schedule; and any decision about the idea or
  sandbox itself: make it a real project, rename it, set it aside, drop it, take it back up, which only Muninn can
  do), as one sentence the user could have said to Muninn; null when it is all the project's.
  end: true when the user is stopping the talk for now and wants it handed back to Muninn.
- an id from others: the whole sentence is plainly about that other conversation. new: it starts something none of them is about.
- When unsure, current."""


# an item named by its number, the way the user calls it: 想法 1, idea1, idea-1 (ideas kept before they had names)
NAMED = (("idea", re.compile(r"(?:idea|想法)\s*[-#]?\s*(\d+)", re.I)),)


@dataclass(frozen=True)
class Choice:
    thread: str | None  # None: a new conversation
    switched: bool  # not the one the user was in
    title: str | None
    about: dict[str, Any] | None = None  # a new conversation's item, when the sentence named one
    for_muninn: str | None = None  # in a lent conversation that it stays in: the part Muninn does beside the talk
    end: bool = False  # in a lent conversation: the user is done with the talk for now (given back once it has answered)


class Topics:
    def __init__(self, store: ThreadStore, clock: Clock, decider: Decider, title: Callable[[dict[str, Any]], str | None],
                 aka: Callable[[dict[str, Any]], str | None] = lambda about: None,
                 named: Callable[[str], dict[str, Any] | None] = lambda said: None, *, words: Language):
        """`decider`: who judges turn_topic (the local model); `title(about)`: an item's name as the user sees it;
        `aka(about)`: what else they call it (an idea's name); `named(said)`: the item a sentence names by such a
        name, None when it names none."""
        self.words = words
        self._store = store
        self._clock = clock
        self._decider = decider
        self._title = title
        self._aka = aka
        self._named_by = named

    def title_of(self, t: dict[str, Any] | None) -> str | None:
        """A conversation's name: the item it is about, else what the model called it, else what the user first said."""
        if not t:
            return None
        if t.get("about"):
            return self._title(t["about"]) or None
        if (t.get("topic") or {}).get("title"):
            return t["topic"]["title"]
        first = next((x["text"] for x in t.get("turns") or [] if x.get("role") == "user"), "")
        return (first[:24] + "…") if len(first) > 24 else first or None

    def choose(self, current: str | None, said: str, *, pinned: bool = False, lent_to: str | None = None,
               lent_talk: Sequence[str] = ()) -> Choice:
        """The conversation `said` goes to. `pinned`: the user chose `current` themselves just now. `lent_to`: the
        project `current` is lent to, and `lent_talk` that talk's latest lines (the user's, the session's and Muninn's, only to tell what
        the user's words refer to); then the same judgment also says what in it is for Muninn (a pinned one only that)."""
        here = self._store.thread(current) if current else None
        if pinned and not lent_to:
            return Choice(current, False, self.title_of(here))
        named = None if pinned else self._named(current, here, said)
        if named is not None:
            return named
        since = (self._clock.now() - GOING).isoformat()
        others = [(tid, t) for tid, t in self._store.threads(CANDIDATES + 1)
                  if tid != current and str(t.get("ts") or "") >= since and any(x.get("role") == "user" for x in t.get("turns") or [])
                  and state(t) not in ("closed", "shelved")][:CANDIDATES]
        if pinned:
            others = []
        if not others and not lent_to:
            return Choice(current, False, self.title_of(here))
        def said_here(t: dict[str, Any] | None) -> list[dict[str, Any]]:  # a lent session's lines too: only the model reads them
            return [x for x in (t or {}).get("turns", []) if x.get("role") in ("user", "muninn")]

        last = lambda t: next((x["text"] for x in reversed(said_here(t))), "")  # noqa: E731
        content = {"said": said,
                   "current": {"title": self.title_of(here) or "(nothing yet)",
                               "last": [f"{'the user' if x['role'] == 'user' else 'Muninn'}:{x['text']}" for x in said_here(here)[-4:]]},
                   "others": [{"id": tid, "title": self.title_of(t), **({"refers_to": n} if t.get("about") and (n := self._aka(t["about"])) else {}),
                               "last": last(t)[:160]} for tid, t in others]}
        if lent_to:  # the talk as it went last: the user's words and the session's, to tell what the user's new sentence refers to
            content["current"]["last"] = list(lent_talk)
        asked: dict[str, Any] = {"content": json.dumps(content, ensure_ascii=False), "labels": ("current", "new", *(tid for tid, _ in others)),
                                 "sensitivity": Sensitivity.SENSITIVE, "domain": None, "field": "topic"}
        verdict = self._decider.decide(Question(point="lent_turn", system=LENT % lent_to, keep=("for_muninn", "end"), **asked) if lent_to
                                       else Question(point="turn_topic", system=SYSTEM, **asked))
        if verdict is None or verdict.label == "current" or pinned:
            part = (verdict.fields.get("for_muninn") if verdict else None) if lent_to else None
            end = bool(lent_to and verdict is not None and verdict.fields.get("end") is True)
            return Choice(current, False, self.title_of(here), for_muninn=part.strip() if isinstance(part, str) and part.strip() else None,
                          end=end)
        if verdict.label == "new":
            return Choice(None, current is not None, None)
        chosen = dict(others)[verdict.label]
        return Choice(verdict.label, True, self.title_of(chosen))

    def _named(self, current: str | None, here: dict[str, Any] | None, said: str) -> Choice | None:
        """A sentence that names an item by its name or number (「接續 shelf-planner 的討論」「idea1」) goes to that
        item's conversation, the latest one about it, or a new one about it; no model is asked. None when it names
        none, or the one on screen."""
        by_name = self._named_by(said)
        numbered = [{"kind": kind, "id": m.group(1)} for kind, pattern in NAMED if (m := pattern.search(said))]
        for about in ([by_name] if by_name else []) + numbered:
            kind = about["kind"]
            if here and here.get("about") and str(here["about"].get("kind")) == kind and str(here["about"].get("id")) == about["id"]:
                return None
            title = self._title(about)
            if title is None:
                return None  # no such item: let the model judge
            found = self._store.threads_about(kind, about["id"])
            if found:
                return Choice(found[0][0], found[0][0] != current, title)
            return Choice(None, True, title, about)
        return None

    def tools(self) -> list[Any]:
        return [SettleTopic(self)]

    # ── where topics stand ──

    def today(self) -> list[dict[str, Any]]:
        """The topics for the panel: every one still open (talked in within a month), and those closed or
        set aside today, newest first."""
        now = self._clock.now()
        day = now.date().isoformat()
        out = []
        for tid, t in self._store.threads(200, None):
            st = state(t)
            ts = str(t.get("ts") or "")
            if ts < (now - timedelta(days=30)).isoformat():
                break
            if st in OPEN or (st and str((t.get("topic") or {}).get("ts") or ts)[:10] == day):
                if not any(x.get("role") == "user" for x in t.get("turns") or []):
                    continue
                out.append({"thread": tid, "about": t.get("about"), "title": self.title_of(t) or self.words("topics.untitled"), "state": st,
                            "now": (t.get("topic") or {}).get("now") or "", "ts": ts, "via": via_of(t),
                            "ask": (t.get("ask") or {}).get("text") or None})
        return out

    def settle(self, said: str, decision: str) -> dict[str, Any]:
        """The user said a topic is done (close), to set it aside (shelve) or to take it back up (reopen);
        the topic is the open one whose name shares most with what they `said`. ValueError when none fits or two do."""
        target = {"close": "closed", "shelve": "shelved", "reopen": "open"}.get(decision)
        if target is None:
            raise ValueError(self.words("topics.which_decision"))
        pool = [(tid, t) for tid, t in self._store.threads(200, None)
                if (state(t) in OPEN) == (decision != "reopen") and any(x.get("role") == "user" for x in t.get("turns") or [])]
        want = _pairs(said)
        scored = [(len(want & _pairs(self.title_of(t) or "")), tid, t) for tid, t in pool]
        best = max([s for s, _, _ in scored], default=0)
        if best == 0:
            raise ValueError(self.words("topics.not_found", said=said))
        top = [(tid, t) for s, tid, t in scored if s == best]
        if len(top) > 1:
            raise ValueError(self.words("topics.which", among=self.words.join(self.title_of(t) or "" for _, t in top[:3])))
        tid, t = top[0]
        self.set_state(tid, t, target)
        return {"thread": tid, "title": self.title_of(t), "state": target}

    def set_state(self, tid: str, t: dict[str, Any], st: str, now: str | None = None) -> None:
        """`t` as read; the change goes onto the conversation as it is when written (another process may have added
        to it meanwhile)."""
        def change(cur: dict[str, Any] | None) -> dict[str, Any]:
            cur = cur or t
            cur["topic"] = {**(cur.get("topic") or {}), "title": self.title_of(cur) or "", "state": st,
                            "ts": now or self._clock.now().isoformat()}
            if st in ("closed", "shelved"):  # done with, or set aside: nothing in it waits on the user
                cur.pop("ask", None)
                cur.pop("talk_left", None)
            return cur

        t.update(self._store.update_thread(tid, change) or {})

    def shelve_quiet(self) -> list[str]:
        """Open topics nobody touched for a week are set aside; their names."""
        cutoff = (self._clock.now() - QUIET).isoformat()
        out = []
        for tid, t in self._store.threads(500, cutoff):
            if state(t) in OPEN and any(x.get("role") == "user" for x in t.get("turns") or []) and not t.get("lease"):
                self.set_state(tid, t, "shelved")
                out.append(self.title_of(t) or self.words("topics.untitled"))
        return out


def via_of(t: dict[str, Any]) -> list[str]:
    """Where the conversation's local words came from (a deep talk, a project): what is said of it goes only there."""
    out: set[str] = set()
    for x in t.get("turns") or []:
        if x.get("local_only"):
            v = x.get("via")
            out |= {v} if isinstance(v, str) else set(v or [])
    return sorted(out)


def state(t: dict[str, Any]) -> str:
    """Where a conversation stands: lent to a project's session is being worked on; it waits on the user only while
    it holds a question asked of them (`ask`); else what was last said of it ("open": talked about, nothing asked);
    "" when nothing was (a conversation from before topics had states)."""
    if t.get("lease"):
        return "doing"
    if t.get("ask"):
        return "waiting"
    st = (t.get("topic") or {}).get("state")
    return "open" if st == "waiting" else st if st in STATES else ""


def _pairs(text: str) -> set[str]:
    t = "".join(text.split())
    return {t[i:i + 2] for i in range(len(t) - 1)} or ({t} if t else set())
