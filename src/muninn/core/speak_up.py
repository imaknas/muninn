"""Muninn speaking up: when the user pauses after talking, it brings up one thing that waits on them, in one
sentence, in the conversation that thing belongs to, and they answer by talking (or with the few buttons
that go with that sentence). Never a list of cards: one thing, once per pause, never in quiet hours, and
the same thing not twice in a day. The rest stays where it is (the panel); the evening says what is open.

And when the user comes back (the app opened on a new day, or after more than ACTIVE away), it opens with what
happened meanwhile: the reminders that came, in one sentence, and the one thing that waits most."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol, Sequence

from .ports import Clock, StateStore
from .language import Language
from .timewin import local_date

PAUSE = timedelta(minutes=20)  # this long without a word is a pause
ACTIVE = timedelta(hours=3)  # longer than this is not a pause but being away: coming back opens with what waits
OPENING_REMINDERS = 3  # reminders named in the opening; the rest are counted


@dataclass(frozen=True)
class Ask:
    """One thing waiting on the user, as Muninn says it."""

    key: str  # what it is, so it is not brought up twice in a day
    text: str  # the one sentence
    about: dict[str, Any] | None = None  # the item the conversation is about (its answer goes there)
    thread: str | None = None  # the conversation it already has, when it has one
    suggest: Sequence[dict[str, Any]] = field(default_factory=tuple)  # the buttons that go with the sentence
    via: Sequence[str] = ()  # where its words came from: what is said goes only there


class Speaker(Protocol):
    def speak(self, about: dict[str, Any] | None, text: str, suggest: Sequence[dict[str, Any]], thread: str | None = None,
              via: Sequence[str] = ()) -> str: ...


class SpeakUp:
    def __init__(self, store: StateStore, clock: Clock, conversation: Callable[[], Speaker], waiting: Callable[[], Sequence[Ask]],
                 quiet: Callable[[datetime], bool], push: Callable[[str], Any],
                 last_heard: Callable[[], str | None] | None = None, *, tz: str = "UTC",
                 reminders: Callable[[], Sequence[dict[str, Any]]] = lambda: (), read: Callable[[Sequence[int]], None] = lambda ids: None,
                 words: Language):
        """`waiting()`: what waits on the user, most pressing first; `quiet(now)`: inside quiet hours;
        `push(title)`: tells the phone (what it says is in the conversation, not the push); `last_heard()`: when
        the user's conversation last went on anywhere (with Muninn, or in a deep talk with a project), the time
        a pause is counted from (by default, when they last said something to Muninn). `reminders()`: the unread
        reminders, newest first (title, ids), which the opening says and `read(ids)` marks read."""
        self.words = words
        self._tz = tz
        self._reminders = reminders
        self._read = read
        self._store = store
        self._clock = clock
        self._conversation = conversation
        self._waiting = waiting
        self._quiet = quiet
        self._push = push
        self._last_heard = last_heard or (lambda: self._store.get_state("talk:last_user"))

    def run(self) -> str | None:
        """Brings up one thing if the user paused; what was said, or None."""
        last = self._last_heard()
        if not last:
            return None
        now = self._clock.now()
        since = now - datetime.fromisoformat(last)
        if since < PAUSE or since > ACTIVE or self._store.get_state("talk:spoke_after") == last or self._quiet(now):
            return None
        self._store.set_state("talk:spoke_after", last)  # one per pause, whether or not there was anything
        said = self._ask(now)
        if said is not None:
            self._push(self.words("speak_up.push"))
        return said

    def opened(self) -> list[str]:
        """The app came into view. After more than ACTIVE away, or on a new day, Muninn opens: the reminders that
        came meanwhile in one sentence, then the one thing that waits most (once a day, as in a pause). What was
        said; nothing when the user was here a moment ago, or nothing waits. Not pushed: the user is looking."""
        now = self._clock.now()
        before = [datetime.fromisoformat(t) for t in (self._store.get_state("talk:last_seen"), self._last_heard()) if t]
        self._store.set_state("talk:last_seen", now.isoformat())
        if before:
            last = max(before)
            if now - last <= ACTIVE and local_date(last, self._tz) == local_date(now, self._tz):
                return []
        said = []
        if reminders := list(self._reminders()):
            shown = reminders[:OPENING_REMINDERS]
            text = self.words("speak_up.while_away", items=self.words.join([r["title"] for r in shown]),
                              more=len(reminders) - len(shown))
            self._conversation().speak(None, text, [])
            self._read([i for r in reminders for i in (r.get("ids") or [r["id"]])])
            said.append(text)
        if (ask := self._ask(now)) is not None:
            said.append(ask)
        if heard := self._last_heard():
            self._store.set_state("talk:spoke_after", heard)  # the pause before coming back is answered by this
        return said

    def _ask(self, now: datetime) -> str | None:
        """Brings up the one thing that waits most and was not brought up today; what was said."""
        today = local_date(now, self._tz).isoformat()
        asked = {k: d for k, d in (self._store.get_state("talk:asked") or {}).items() if d == today}
        ask = next((a for a in self._waiting() if a.key not in asked), None)
        if ask is None:
            return None
        self._conversation().speak(ask.about, ask.text, list(ask.suggest), thread=ask.thread, via=ask.via)
        self._store.update_state("talk:asked", lambda now: {**{k: d for k, d in (now or {}).items() if d == today}, ask.key: today})
        return ask.text
