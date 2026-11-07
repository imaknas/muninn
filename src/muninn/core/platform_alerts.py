"""Alerts from the platform Muninn runs on (contracts/platform-alerts.v1.md): a problem is one of the reminders while
it is open, said again each time the platform says it again, pushed outside the daily push limit but held through
quiet hours; when it is resolved its reminder goes and what works again is said once, without a push."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol

from .models import InboxItem
from .ports import Clock, InboxStore, StateStore

STATE = "platform-alerts"
DOMAIN = "platform"
SOURCE = re.compile(r"[a-z0-9_-]{1,64}")


class Refused(ValueError):
    """A notice that does not keep to the contract: why."""


@dataclass(frozen=True)
class Notice:
    source: str
    key: str
    state: str  # open | resolved
    at: datetime
    text: str
    detail: str = ""

    @property
    def problem(self) -> str:
        return f"{self.source}/{self.key}"


def notice(raw: Any) -> Notice:
    """A posted notice as Muninn reads it; Refused, with why, when it does not keep to the contract."""
    if not isinstance(raw, Mapping):
        raise Refused("a notice is a JSON object")
    if raw.get("v") != 1:
        raise Refused("v: 1")
    source, key, state, text, detail = (raw.get(f) for f in ("source", "key", "state", "text", "detail"))
    if not isinstance(source, str) or not SOURCE.fullmatch(source):
        raise Refused("source: lowercase letters, digits, - and _, at most 64")
    if not isinstance(key, str) or not key.strip() or len(key) > 200:
        raise Refused("key: a name for the problem, at most 200 characters")
    if state not in ("open", "resolved"):
        raise Refused("state: open or resolved")
    if not isinstance(text, str) or not text.strip() or "\n" in text.strip():
        raise Refused("text: one line")
    if detail is not None and not isinstance(detail, str):
        raise Refused("detail: text")
    try:
        at = datetime.fromisoformat(str(raw.get("at")).replace("Z", "+00:00"))
    except ValueError as e:
        raise Refused("at: an RFC 3339 time") from e
    if at.tzinfo is None:
        raise Refused("at: needs a time zone")
    return Notice(source, key, state, at, text.strip(), (detail or "").strip())


class PlatformAlertStorage(StateStore, InboxStore, Protocol):
    """Where each problem's last notice is kept, and the reminders."""


class PlatformAlerts:
    def __init__(self, store: PlatformAlertStorage, clock: Clock, quiet: Callable[[datetime], bool],
                 push: Callable[[str], Any]):
        """`quiet(now)`: whether pushes are held now; `push(title)`: pushes one notification."""
        self._store, self._clock, self._quiet, self._push = store, clock, quiet, push

    def jobs(self) -> list[Any]:
        from .actors import Job

        return [Job("pushing the platform's alerts held through quiet hours", self.release_held)]

    def receive(self, n: Notice) -> str:
        """What the notice did (the contract's outcomes). Kept first, then said: when saying it fails, what was kept
        is put back, so the platform's retry is not taken for a notice already had."""
        now = self._clock.now()
        held = self._quiet(now)
        outcome = "repeat"
        before: dict[str, Any] | None = None

        def change(all_: Any) -> dict[str, Any]:
            nonlocal outcome, before
            all_ = dict(all_ or {})
            before = all_.get(n.problem)
            if before is not None and datetime.fromisoformat(before["at"]) >= n.at:
                return all_
            was_open = before is not None and before["state"] == "open"
            if n.state == "open":
                outcome = "reminded" if was_open else "new"
            else:
                outcome = "resolved" if was_open else "not_open"
            all_[n.problem] = {"state": n.state, "at": n.at.isoformat(), "text": n.text,
                               "held": n.state == "open" and held}
            return all_

        self._store.update_state(STATE, change)
        if outcome == "repeat":
            return outcome
        try:
            if n.state == "open":
                self._remind(n, now, held)
            elif outcome == "resolved":
                self._resolve(n, now)
        except Exception:
            self._store.update_state(STATE, lambda all_: {**(all_ or {}), n.problem: before} if before is not None
                                     else {k: v for k, v in (all_ or {}).items() if k != n.problem})
            raise
        return outcome

    def _remind(self, n: Notice, now: datetime, held: bool) -> None:
        # one signal key per problem: the reminders show it once, with how many times it was said
        self._store.add_inbox(InboxItem(None, now, DOMAIN, "alert", n.text, n.detail,
                                        {"platform": {"source": n.source, "key": n.key, "at": n.at.isoformat()},
                                         "quiet_hold": held},
                                        signal_key=_signal(n)))
        if not held:
            self._push(n.text)

    def _resolve(self, n: Notice, now: datetime) -> None:
        self._store.mark_read([i.id for i in self._store.inbox(limit=500, unread_only=True)
                               if i.signal_key == _signal(n) and i.id is not None])
        self._store.add_inbox(InboxItem(None, now, DOMAIN, "alert", n.text, n.detail,
                                        {"platform": {"source": n.source, "key": n.key, "at": n.at.isoformat()}},
                                        signal_key=_signal(n) + ":resolved"))

    def release_held(self) -> list[str]:
        """Once quiet hours are over: the problems still open whose push they held, pushed now."""
        now = self._clock.now()
        if self._quiet(now):
            return []
        due: list[str] = []

        def change(all_: Any) -> dict[str, Any]:
            all_ = dict(all_ or {})
            for problem, p in all_.items():
                if p.get("held") and p["state"] == "open":
                    due.append(p["text"])
                    all_[problem] = {**p, "held": False}
            return all_

        if any(p.get("held") for p in (self._store.get_state(STATE) or {}).values()):
            self._store.update_state(STATE, change)
        for text in due:
            self._push(text)
        return due


def _signal(n: Notice) -> str:
    # not split on ":" into a plan's goal or deadline (Views.alerts): the problem is one part
    return f"platform:{n.problem}"
