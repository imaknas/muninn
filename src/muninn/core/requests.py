"""What the user asked of Muninn itself (the request list), and when each became part of the Muninn that runs.

Each request is kept in state "feature_requests" as {ts, text, done}, with fields added since that older code reads
past: id; thread (the conversation it was asked in, kept here since a handoff is cleaned away in time); via (the deep
talk's content the round carried, whose project alone may hear of it); handoff (the handoff it went out as); commit
(what implements it); live (when the running Muninn first included it); live_by ("commit"); refused (a commit the
user said did not do it). `done` means live, so
older code reading the list sees only what is still open. A request is live only when the running Muninn contains its
commit: that is looked at when the server starts and every so often, and said once in its conversation (undoable;
undone, that commit no longer counts for it). A deploy rolled back that took a live request's commit with it puts the
request back to not live, said once there too; a rollback no live request rode on is said nowhere.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Callable, Protocol, Sequence

from .actions import ActionGate, DecidedBy, RequestLive
from .ports import Clock, LeaseStore, RunningVersion, StateStore, ThreadStore
from .language import Language

log = logging.getLogger(__name__)

KEY = "feature_requests"
ROLLBACKS = "feature_requests:rollbacks"  # the deploy log's rollback lines already acted on
# how Muninn said it kept one before requests kept their conversation: the turn such a request is found by (those turns
# were said in Chinese; what Muninn says now is requests.recorded, in the language it speaks)
RECORDED = "記到需求清單了"
NEAR_S = 5  # how close that turn is to the request's own time
ROLLBACK = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d rollback (\S+) -> (\S+?):?(?: (.*))?$")


def rollback(line: str) -> tuple[str, str, str] | None:
    """A deploy log line read as a rollback: (bad, previous, reason); None for any other line."""
    m = ROLLBACK.match(line.strip())
    return (m.group(1), m.group(2), (m.group(3) or "").strip()) if m else None


class RequestStorage(StateStore, ThreadStore, LeaseStore, Protocol):
    """The requests, and the conversations and deep talks they were asked in."""


class Requests:
    def __init__(self, store: RequestStorage, clock: Clock, gate: ActionGate, version: RunningVersion,
                 speak: Callable[[str | None, str, Sequence[str], list[dict[str, Any]]], Any], push: Callable[[str], Any] = lambda title: None, *, words: Language):
        """`version`: which Muninn runs; `speak(thread, text, via, buttons)`: Muninn says it in that conversation (a new
        one when None), kept to `via`'s project when it came from a deep talk; `push(title)`: tells the phone (quiet hours are
        its own to keep)."""
        self.words = words
        self._store = store
        self._clock = clock
        self._gate = gate
        self.version = version
        self._speak = speak
        self._push = push

    def _all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._store.get_state(KEY) or []]

    def _save(self, reqs: list[dict[str, Any]]) -> None:
        self._store.set_state(KEY, reqs)

    def add(self, text: str, thread: str | None = None, via: Sequence[str] = ()) -> int:
        """Keeps the request, with the conversation it was asked in and the deep talk that round carried; how many are
        open."""
        reqs = self._all()
        rid = max((int(r.get("id") or 0) for r in reqs), default=0) + 1
        reqs.append({"ts": self._clock.now().isoformat(), "text": text[:500], "done": False, "id": rid, "thread": thread,
                     "via": sorted(via), "handoff": None, "commit": None, "live": None, "live_by": None})
        self._save(reqs)
        return sum(1 for r in reqs if not r["done"])

    def open(self) -> list[dict[str, Any]]:
        return [r for r in self._all() if not r["done"]]

    def all(self) -> list[dict[str, Any]]:
        return self._all()

    def set_commit(self, rid: int, commit: str) -> bool:
        """The commit that implements an open request (the next check marks it live once that commit runs); False when
        there is no such open request."""
        reqs = self._all()
        r = next((x for x in reqs if x.get("id") == rid and not x["done"]), None)
        if r is None:
            return False
        r["commit"] = commit
        self._save(reqs)
        return True

    def set_live(self, rid: int, commit: str) -> dict[str, Any] | None:
        """Marked live by that commit; None when it is not a request waiting on that commit."""
        reqs = self._all()
        r = next((x for x in reqs if x.get("id") == rid), None)
        if r is None or r["done"] or r.get("commit") != commit:
            return None
        r.update(done=True, live=self._clock.now().isoformat(), live_by="commit")
        self._save(reqs)
        return r

    def not_live(self, rid: int, refused: str | None = None) -> bool:
        """Back to not live; `refused`: a commit that no longer counts for it (the user said it was not done by it)."""
        reqs = self._all()
        r = next((x for x in reqs if x.get("id") == rid), None)
        if r is None:
            return False
        r.update(done=False, live=None, live_by=None)
        if refused:
            r["refused"] = refused
        self._save(reqs)
        return True

    def migrate(self) -> list[dict[str, Any]]:
        """Requests kept before these fields: each gets an id, the conversation it was asked in (found by the turn
        where Muninn said it kept it, or none). Runs again harmlessly: only what
        is missing is filled. The requests as they now are."""
        reqs = self._all()
        changed = False
        top = max((int(r.get("id") or 0) for r in reqs), default=0)
        for r in reqs:
            if not r.get("id"):
                top += 1
                r["id"] = top
                changed = True
            if "thread" not in r:
                found = self._said_at(str(r.get("ts") or ""))
                r["thread"], r["via"] = found if found else (None, r.get("via") or [])
                changed = True
            for k in ("handoff", "commit", "live", "live_by"):
                if k not in r:
                    r[k] = None
                    changed = True
        if changed:
            self._save(reqs)
        return reqs

    def _said_at(self, ts: str) -> tuple[str, list[str]] | None:
        """The conversation where Muninn said it kept a request at `ts`, and the deep talk it was said in, if any."""
        try:
            when = datetime.fromisoformat(ts)
        except ValueError:
            return None

        def near(t: dict[str, Any]) -> bool:
            try:
                said = str(t.get("text") or "")
                return t.get("role") == "muninn" and (RECORDED in said or self.words("requests.recorded") in said) and \
                    abs((datetime.fromisoformat(str(t.get("ts"))) - when).total_seconds()) <= NEAR_S
            except (TypeError, ValueError):
                return False

        for lease in self._store.leases(active=False):  # said beside a deep talk: kept to that talk
            if any(near(t) for t in self._store.lease_turns(lease["lease_id"])):
                return str(lease["thread"]), [f"lease:{lease['lease_id']}"]
        for tid, t in self._store.threads(1000):
            turn = next((x for x in t.get("turns") or [] if near(x)), None)
            if turn is not None:
                via = turn.get("via") if turn.get("local_only") else []
                return tid, sorted([via] if isinstance(via, str) else via or [])
        return None

    def check(self, locked: Callable[[Callable[[], Any]], Any]) -> int:
        """Looks at what runs now: a request whose commit it contains is marked live and said so in its conversation;
        a rollback in the deploy log not acted on before puts live requests whose commit no longer runs back to not
        live, said there with its reason. `locked(fn)` runs fn where threads are written; the version is read outside
        it. How many requests changed."""
        if self.version.unknown:
            return 0
        reqs = locked(self.migrate)
        waiting = [r for r in reqs if not r["done"] and r.get("commit") and r.get("refused") != r["commit"]]
        now_live = [r for r in waiting if self.version.contains(str(r["commit"])) is True]
        rollbacks = [x for x in self.version.deploy_lines() if rollback(x)]
        live = [r for r in reqs if r["done"] and r.get("live_by") == "commit" and r.get("commit")]
        # gone only when the running history knows the commit and it is not in it: one from a history started afresh
        # (the old one archived) is not known either way, and stays as it was
        gone = {r["id"] for r in live if self.version.contains(str(r["commit"])) is False} if rollbacks else set()

        def apply() -> tuple[int, int]:
            went, back = 0, 0
            for r in now_live:
                ran = self._gate.run(RequestLive(int(r["id"]), str(r["commit"]), str(r["text"])), DecidedBy.INFERRED_SURE)
                if ran is not None:
                    self._speak(r.get("thread"), self.words("requests.live", text=r["text"]), r.get("via") or [],
                                [{"label": self.words("requests.not_yet"), "tool": "undo_action", "args": {"what": ran.text}}])
                    went += 1
            acted = set(self._store.get_state(ROLLBACKS) or [])
            new = [x for x in rollbacks if x not in acted]
            if not new:
                return went, back
            _, _, reason = rollback(new[-1]) or ("", "", "")
            for r in self._all():
                if r.get("id") in gone and r["done"] and self.not_live(int(r["id"])):
                    self._speak(r.get("thread"), self.words("requests.rolled_back", reason=reason, text=r["text"]), r.get("via") or [], [])
                    back += 1
            self._store.set_state(ROLLBACKS, sorted(acted | set(new)))
            return went, back

        went, back = locked(apply)
        if went or back:
            self._push(self.words("requests.push_rolled_back") if back else self.words("requests.push_updated"))
        return went + back
