"""The conversation as an actor: the threads (a discussion, about an item or not), one turn of
it understood and carried out, a next step the discussion offered pressed, and the latest
discussion about an item. It knows neither which actors there are nor how they keep things:
the command runner brings their tools, the topic router their topics."""

from __future__ import annotations

import secrets
from typing import Any, Callable, Protocol, Sequence

from .commands import CommandResult, CommandRunner
from .models import Attachment
from .ports import Clock, StateStore, ThreadStore
from .topic_memory import TopicMemory
from .language import Language

FEED = 30  # how many of the latest conversations the stream opens with
# what answers the item a conversation is about (a question, a goal to confirm): once the item is settled, by a press,
# a typed answer or its card, these buttons have nothing left to answer
ANSWERS = frozenset({"answer_question", "decide_item"})
SETTLED = ("question", "project_question", "review")  # the items that are gone once answered


class ConversationStorage(ThreadStore, StateStore, Protocol):
    """The conversations, and when the user last said something to Muninn (state "talk:last_user": when they paused)."""


class Conversation:
    def __init__(self, store: ConversationStorage, clock: Clock, commands: Callable[[], CommandRunner],
                 topic: Callable[[str, str], dict[str, Any] | None], memory: TopicMemory | None = None,
                 spoke_in: Callable[[str], None] = lambda tid: None, *, words: Language):
        """`commands`: the command runner, with the actors' tools as they are now; `topic(kind, ref)`:
        what the model sees about the item discussed; `memory`: what of a long conversation a round reads
        (None: its latest lines); `spoke_in(thread)`: the user just spoke to Muninn in that conversation (so
        one lent elsewhere was left). Every conversation is kept for good."""
        self.words = words
        self._store = store
        self._clock = clock
        self._commands = commands
        self._topic = topic
        self._memory = memory
        self._spoke_in = spoke_in

    def thread(self, tid: str) -> dict[str, Any] | None:
        return self._store.thread(tid)

    def understand_turn(self, tid: str | None, text: str, about: dict[str, str] | None = None,
                        attachments: Sequence[Attachment] = (), beside: dict[str, Any] | None = None) -> dict[str, Any]:
        """The slow part of one turn of a conversation (outside the lock). `attachments`: the files sent with it;
        `beside`: the deep talk it is said beside ({lease, project, talk}), when Muninn does its part of a sentence
        said there."""
        t = self.thread(tid) if tid else None
        if t is None:
            tid = secrets.token_hex(6)
            t = {"about": about, "turns": []}
        ab = self._topic(t["about"]["kind"], str(t["about"]["id"])) if t.get("about") else None
        view = self._memory.view(tid, t["turns"], text) if self._memory else None
        plan = self._commands().understand(text, history=t["turns"], about=ab, talk=bool(t["turns"] or t.get("about")), view=view,
                                           marks=t.get("marks") or (),
                                           attachments=attachments, beside=beside)
        return {"thread": tid, "state": t, "text": text, "plan": plan, "files": [a.meta for a in attachments]}

    def execute_turn(self, turn: dict[str, Any]) -> CommandResult:
        # as it is now, not as it was before the model call (a lease may have been attached meanwhile)
        t = self.thread(turn["thread"]) or turn["state"]
        r = self._commands().execute({**turn["plan"], "made": t.get("made"), "thread": turn["thread"]} if turn["plan"] else None)
        plan = turn["plan"] or {}
        now = self._clock.now().isoformat()
        # a local round's reply may repeat what must stay local, and where it came from goes along
        local = {"local_only": True, "via": plan.get("via") or []} if plan.get("local_only") else {}

        def said(t: dict[str, Any] | None) -> dict[str, Any]:
            t = t or turn["state"]  # as it is now: what the round's tools did to it, or another process meanwhile
            self._remember(t, r)
            t["turns"] = t["turns"] + [
                {"role": "user", "text": turn["text"], "ts": now, "cloud_ok": bool(plan.get("cloud_ok")), **local,
                 **({"files": turn["files"]} if turn.get("files") else {})},
                {"role": "muninn", "text": "\n".join(x for x in [r.reply, *(o.text for o in r.outcomes)] if x), "ts": now, **local,
                 **({"stood_in": True} if r.stood_in else {})},
            ]
            t["ts"] = now
            t["suggest"] = stamped(r.suggest)  # the latest next steps the discussion led to
            mark(t, *(plan.get("mark") or []))
            if r.topic:  # what it is about and where it stands, as of this round
                t["topic"] = {**r.topic, "ts": now}
            if turn["plan"] and r.understood:  # what it now waits on him for; none: it went on from whatever was asked before
                if r.question:
                    t["ask"] = {"text": r.question, "ts": now, **({"via": list(plan.get("via") or [])} if plan.get("local_only") else {})}
                else:
                    t.pop("ask", None)
            return t

        self.update_thread(turn["thread"], said)
        self._store.set_state("talk:last_user", now)
        self._spoke_in(turn["thread"])
        return r

    def speak(self, about: dict[str, Any] | None, text: str, suggest: Sequence[dict[str, Any]], thread: str | None = None,
              marks: Sequence[str] = (), via: Sequence[str] = ()) -> str:
        """Muninn says something unasked, in `thread`, else the latest conversation about `about`, else a new one
        about it; `suggest`: the buttons that go with it, kept with what it says (`turn`), beside those of what Muninn
        said unasked before (until he talks there or presses one of a line's own); `marks`: what the conversation is marked with from now
        on (a question asked once there); `via`: where what it says came from ("lease:<id>", "project:<name>"), which
        alone may hear of it again. The thread's id."""
        tid = thread if thread and self.thread(thread) is not None else None
        if tid is None and about:
            found = self._store.threads_about(about["kind"], str(about["id"]))
            tid = found[0][0] if found else None
        tid = tid or secrets.token_hex(6)
        now = self._clock.now().isoformat()
        local = {"local_only": True, "via": sorted(via)} if via else {}

        def said(t: dict[str, Any] | None) -> dict[str, Any]:
            t = t or {"about": about, "turns": []}
            # a line said again (a question still open the next day) has its buttons with the latest saying, not twice
            again = {s["turn"] for s in t.get("suggest") or [] if s.get("turn") and any(same(s, n) for n in suggest)}
            kept = [s for s in t.get("suggest") or [] if s.get("turn") and s["turn"] not in again]
            t["turns"] = t["turns"] + [{"role": "muninn", "text": text, "ts": now, "spoken": True, **local}]
            t["ts"] = now
            t["suggest"] = kept + stamped(suggest, now)
            mark(t, *marks)
            return t

        self.update_thread(tid, said)
        return tid

    def settle_ask(self, tid: str) -> dict[str, Any] | None:
        """What the conversation asked of him is past (it went on elsewhere): the question it held, or None."""
        held: list[dict[str, Any]] = []

        def settle(t: dict[str, Any] | None) -> dict[str, Any] | None:
            if t is None or not t.get("ask"):
                return None
            held.append(t.pop("ask"))
            return t

        self.update_thread(tid, settle)
        return held[0] if held else None

    def restore_ask(self, tid: str, ask: dict[str, Any]) -> bool:
        return self.update_thread(tid, lambda t: {**t, "ask": ask} if t is not None else None) is not None

    def save_thread(self, tid: str, t: dict[str, Any]) -> None:
        self._store.save_thread(tid, t)

    def update_thread(self, tid: str, change: Callable[[dict[str, Any] | None], dict[str, Any] | None]) -> dict[str, Any] | None:
        """How a conversation is changed: `change` gets it as it is at that moment (another process may have added
        to it), and what it returns is kept (None: nothing)."""
        return self._store.update_thread(tid, change)

    def live(self, t: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
        """The next steps still to be pressed, each with its place in the kept list: the buttons answering the item
        the conversation is about go once it is settled, wherever it was answered."""
        about = t.get("about") or {}
        settled = about.get("kind") in SETTLED and self._topic(about["kind"], str(about["id"])) is None
        return [(i, s) for i, s in enumerate(t.get("suggest") or []) if not (settled and s.get("tool") in ANSWERS)]

    def act(self, tid: str, index: int | None = None, *, button: str | None = None) -> CommandResult:
        """The user pressed one of the next steps a discussion offered: `button`, its id (what the screen saw, however
        the list changed since); `index`, its place, from a screen that has no ids."""
        t = self.thread(tid)
        live = self.live(t) if t is not None else []
        s = next((x for i, x in live if (x.get("id") == button if button is not None else i == index)), None)
        if t is None or s is None:
            raise ValueError(self.words("conversation.suggestion_gone"))
        ab = self._topic(t["about"]["kind"], str(t["about"]["id"])) if t.get("about") else None
        r = self._commands().execute({"actions": [{"tool": s["tool"], "args": dict(s.get("args") or {})}], "reply": "",
                                         "about": ab, "made": t.get("made")})
        now = self._clock.now().isoformat()

        def pressed(t: dict[str, Any] | None) -> dict[str, Any] | None:
            if t is None:
                return None
            self._remember(t, r)
            local = {"local_only": True} if any(x.get("local_only") for x in t["turns"][-12:]) else {}
            t["turns"] = t["turns"] + [
                {"role": "user", "text": self.words("conversation.pressed", label=s["label"]), "ts": now, "cloud_ok": True, **local},
                {"role": "muninn", "text": "\n".join(o.text for o in r.outcomes) or self.words("conversation.done"), "ts": now, **local},
            ]
            # pressing one is a round of its own: the next steps are what it led to (often none), beside the buttons of
            # the other lines Muninn said unasked (the pressed line's own go: it is answered, and so is the same line
            # said before)
            answered = {x["turn"] for x in t.get("suggest") or [] if x.get("turn") and (x["turn"] == s.get("turn") or same(x, s))}
            t["suggest"] = [x for x in t.get("suggest") or [] if x.get("turn") and x["turn"] not in answered] + stamped(r.suggest)
            t["ts"] = now
            return t

        self.update_thread(tid, pressed)
        self._store.set_state("talk:last_user", now)  # (a button answers one thing: it does not leave a deep talk)
        return r

    @staticmethod
    def _remember(t: dict[str, Any], r: CommandResult) -> None:
        """What a round brought into being stays with the conversation, so its next steps (offered
        before it had a number) find it; a conversation about nothing in particular is now about it."""
        if r.made:
            t["made"] = r.made
            if not t.get("about"):
                t["about"] = r.made

    def feed(self, before: str | None = None, limit: int = FEED) -> list[dict[str, Any]]:
        """The latest conversations (talked in before `before`), oldest first, for the one stream the user
        sees: each with what it is about (its title as the item says it), its turns, the next steps it
        left, and the deep talk it is lent to. What Muninn said beside a deep talk is shown in that talk, not again."""
        out = []
        for tid, t in reversed(self._store.threads(limit, before)):
            about = t.get("about")
            seen = self._topic(about["kind"], str(about["id"])) if about else None
            out.append({"thread": tid, "about": about,
                        "title": ((seen or {}).get("title") if about else None) or (t.get("topic") or {}).get("title") or None,
                        "ts": t.get("ts"), "lease_id": t.get("lease"),
                        "turns": [{"role": x["role"], "text": x["text"], "ts": x.get("ts"), **({"spoken": True} if x.get("spoken") else {}),
                                   **({"files": x["files"]} if x.get("files") else {}),
                                   **({"handover": x["handover"]} if x.get("handover") else {}),
                                   **({"ended": ended} if (ended := ended_talk(x)) else {}),
                                   **({"note": True} if x.get("note") else {})}
                                  for x in t.get("turns") or []
                                  if x.get("role") in ("user", "muninn") and not x.get("beside")],
                        "suggest": self.buttons(t)})
        return out

    def buttons(self, t: dict[str, Any], *, pending: bool = False) -> list[dict[str, Any]]:
        """The next steps a screen draws and presses: label, id (index: from before ids were kept), the line it goes
        with. `pending`: the item is known to wait still (its card is being drawn), so none is left out."""
        return [{"index": i, "label": s.get("label", ""), **{k: s[k] for k in ("id", "turn", "plain") if s.get(k)}}
                for i, s in (enumerate(t.get("suggest") or []) if pending else self.live(t))]

    def suggestions_for(self, kind: str, ref: str) -> dict[str, Any] | None:
        """The latest discussion about an item still waiting (its card), with the next steps it left. What Muninn said
        about it unasked (bringing it up, again the next day) is the item itself, which the card already shows with
        its own answers: not what a discussion left, and with no word from the user there is no discussion at all."""
        talks = self._store.threads_about(kind, str(ref))
        if not talks:
            return None
        tid, t = talks[0]
        if not any(x["role"] == "user" for x in t["turns"]):
            return None
        return {"thread": tid, "suggest": [b for b in self.buttons(t, pending=True) if not b.get("turn")],
                "last": next((x["text"] for x in reversed(t["turns"]) if x["role"] == "muninn" and not x.get("spoken")), "")}


def stamped(suggest: Sequence[dict[str, Any]], turn: str | None = None) -> list[dict[str, Any]]:
    """Next steps as kept: each with an id of its own, which a press names (the list changes under an open screen);
    `turn`: the line Muninn said unasked they go with."""
    return [{**s, "id": secrets.token_hex(4), **({"turn": turn} if turn else {})} for s in suggest]


def same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Two buttons that do the same thing."""
    return a.get("tool") == b.get("tool") and (a.get("args") or {}) == (b.get("args") or {})


def mark(t: dict[str, Any], *marks: str) -> None:
    """Marks the conversation (a question asked once there, so it is not asked again)."""
    if marks:
        t["marks"] = sorted({*(t.get("marks") or []), *marks})


def ended_talk(turn: dict[str, Any]) -> str | None:
    """The deep talk a turn says has ended (its handover, or that it broke off): the lease it came back from (turns
    kept before they said so: read from where they came from, and from the words Muninn said it with then, which were
    always Chinese)."""
    if isinstance(turn.get("ended"), str):
        return turn["ended"]
    via = turn.get("via")
    if turn.get("role") == "muninn" and isinstance(via, str) and via.startswith("lease:") and (
            turn.get("handover") or "的深談" in str(turn.get("text") or "")):  # legacy turns: matched as stored, in Chinese
        return via.removeprefix("lease:")
    return None
