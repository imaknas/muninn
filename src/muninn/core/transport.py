"""Muninn as the server of Principal Handoff Transport 0.1 (experimental; contracts/ph-transport.md).

Clients (the projects' agents and registrars, or a platform relaying for them) send records, fetch a project's log from
a cursor, and acknowledge what they have taken. The log holds every record Muninn stores about a project and every
record it makes, once it will not take it back: a settlement held for an undo enters when the undo window ends, and an
undo after that is a new record. A fetch returns records of every origin, the client's own included.

Who may send what is the credential's: each speaks for a list of parties, or for every party the role configuration
gives a role in a project (a relay). The role configuration is open: every party but the principal and Muninn holds
the requester, agent and registrar roles in every project. Muninn is the only principal side, so no client sends a
principal record.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence

from . import ph
from .handoff_records import Inbox, Outbox
from .ports import Clock, RecordLogStore, RecordStore, StateStore

GENERATION = "ph:log:generation"  # state: the log's generation; a new one makes every cursor before it unknown
BACKFILLED = "ph:log:backfilled"  # state: the records held before the log existed are in it
ROLES = ("requester", "agent", "registrar")  # the roles a client's party may hold (the principal side is Muninn)
CURSOR = re.compile(r"g([0-9]{1,9})\.([0-9]{1,18})")
MAX_RECORDS = 500  # per send
MAX_RECORD_BYTES = 256 * 1024
LIMIT = 200  # records per answer, when the client asks for more or names no limit (Transport section 8.4: at least 100)


class TransportError(Exception):
    """An error answer (Transport section 8.5)."""

    def __init__(self, status: int, code: str, message: str, path: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.path = status, code, message, path

    def body(self) -> dict[str, Any]:
        return {"errors": [{"code": self.code, "message": self.message, **({"path": self.path} if self.path else {})}]}


@dataclass(frozen=True)
class Credential:
    """What a client presents, and the parties it may speak for: those named, or (None) every party the role
    configuration gives a role in a project, for a platform that relays its projects' agents and registrar."""

    name: str
    parties: frozenset[str] | None = None

    def single(self) -> str | None:
        """The one party it speaks for, when it speaks for one only."""
        return next(iter(self.parties)) if self.parties is not None and len(self.parties) == 1 else None


class Roles:
    """Which parties hold which roles in which projects (core section 1), as this deployment has it: every party but
    the principal and Muninn holds every client role in every project."""

    def __init__(self, parties: ph.Parties):
        self._parties = parties

    def holds(self, party: Any, role: Any, project: Any) -> bool:
        return (isinstance(party, str) and bool(party) and party not in (self._parties.principal, self._parties.side)
                and role in ROLES and isinstance(project, str) and bool(ph.PROJECT.fullmatch(project)))


class LogStore(RecordLogStore, RecordStore, StateStore, Protocol):
    """The logs, the records they began from, and the log's generation."""


class Transport:
    def __init__(self, store: LogStore, clock: Clock, parties: ph.Parties, inbox: Inbox, outbox: Outbox, roles: Roles):
        self._store = store
        self._clock = clock
        self._parties = parties
        self._inbox = inbox
        self._outbox = outbox
        self._roles = roles

    # the log

    def backfill(self) -> None:
        """Once: what Muninn held before the log existed enters it, oldest first, so that a client reading from the
        beginning has everything still kept (the forms an id reused displaced, and handoffs pruned, are not)."""
        if self._store.get_state(BACKFILLED):
            return
        received = [(when, 0, r) for when, r in self._store.records_received()]
        made = [(when, hid, r) for when, hid, r in self._outbox.due()]
        for when, hid, r in sorted(received + made, key=lambda x: x[0]):
            if hid:
                self._outbox.logged(hid, r)
            elif isinstance(r.get("project"), str):
                self._store.log_append(r["project"], r, ph.digest(r), self._clock.now(), own=False)
        self._outbox.caught_up()
        self._store.set_state(BACKFILLED, self._clock.now().isoformat())

    def catch_up(self, *_: Any) -> None:
        """Muninn's records that came due enter the logs (after every handoff it adds, and before every read)."""
        self.backfill()
        for _when, hid, r in self._outbox.due():
            self._outbox.logged(hid, r)
        self._outbox.caught_up()

    def generation(self) -> int:
        return int(self._store.get_state(GENERATION) or 1)

    def new_generation(self) -> int:
        """After a log lost records (an older copy restored): every cursor before becomes unknown and the
        acknowledgements are dropped, so clients read again from the beginning and send again what is missing."""
        gen = self._store.update_state(GENERATION, lambda was: int(was or 1) + 1)
        self._store.log_forget_acks()
        return int(gen)

    def _cursor(self, pos: int | None) -> str | None:
        return None if pos is None else f"g{self.generation()}.{pos}"

    def _position(self, project: str, cursor: Any) -> int:
        m = CURSOR.fullmatch(cursor) if isinstance(cursor, str) else None
        if not m or int(m.group(1)) != self.generation() or not self._store.log_holds(project, int(m.group(2))):
            raise TransportError(400, "unknown_cursor", "the cursor names no position of this log; read from the beginning")
        return int(m.group(2))

    # who

    def speaks_for(self, cred: Credential, party: Any, project: Any) -> bool:
        """The credential may speak for `party`, a party holding a role in `project`."""
        if not any(self._roles.holds(party, role, project) for role in ROLES):
            return False
        return cred.parties is None or party in cred.parties

    def _party(self, cred: Credential, project: str, as_: Any) -> str:
        if not isinstance(project, str) or not ph.PROJECT.fullmatch(project):
            raise TransportError(400, "bad_parameter", "project is 1 to 128 characters without whitespace", "/project")
        party = as_ if as_ is not None else cred.single()
        if party is None:
            raise TransportError(400, "bad_parameter", "as is required: the credential speaks for more than one party", "/as")
        if not self.speaks_for(cred, party, project):
            raise TransportError(403, "no_role", "the credential may not speak for that party in that project")
        return str(party)

    # the operations

    def send(self, cred: Credential, records: Sequence[Any]) -> list[dict[str, Any]]:
        """One result per record, the nth for the nth (Transport section 4)."""
        if len(records) > MAX_RECORDS:
            raise TransportError(413, "too_large", f"at most {MAX_RECORDS} records per send")
        out = []
        for r in records:
            rid = r.get("id") if isinstance(r, dict) and isinstance(r.get("id"), str) else None
            if not isinstance(r, dict):
                out.append({"status": "invalid", "errors": [_error("not an object")]})
                continue
            by, origin, project = r.get("by"), r.get("origin"), r.get("project")
            if origin == "principal" or not self.speaks_for(cred, by, project) or not self._roles.holds(by, origin, project):
                out.append({"status": "not_authorised", **_id(rid)})
                continue
            if len(ph.canonical(r).encode()) > MAX_RECORD_BYTES:
                out.append({"status": "too_large", **_id(rid)})
                continue
            got = self._inbox.take(r)
            out.append({"status": got["status"], **_id(rid), **({"reason": got["reason"]} if got.get("reason") else {}),
                        **({"errors": [_error(e) for e in got.get("errors") or []]} if got["status"] == "invalid" else {})})
        self.catch_up()  # what Muninn did on them may have made records
        return out

    def fetch(self, cred: Credential, project: str, after: str | None, as_: str | None, limit: int | None,
              told: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
        """The project's records after the cursor (from the beginning without one), in log order (Transport section
        5.2). Muninn's own are told in the version configured for the project; every other as it was sent."""
        party = self._party(cred, project, as_)
        self.catch_up()
        pos = self._position(project, after) if after is not None else 0
        n = min(limit or LIMIT, LIMIT)
        got = self._store.log_after(project, pos, n + 1)
        more, got = len(got) > n, got[:n]
        tell = told or self._outbox.told
        records = [tell(x["record"]) if x["own"] else x["record"] for x in got]
        last = got[-1]["pos"] if got else (pos if after is not None else None)
        return {"records": records, "cursor": self._cursor(last), "more": more,
                "acked": self._cursor(self._store.log_acked(project, party))}

    def ack(self, cred: Credential, project: str, through: Any, as_: str | None) -> str:
        """The party has durably taken the project's records up to `through`. It changes nothing but where the
        project resumes (Transport section 6); one before the cursor already acknowledged is ignored."""
        party = self._party(cred, project, as_)
        pos = self._position(project, through)
        return str(self._cursor(self._store.log_ack(project, party, pos)))


def _id(rid: str | None) -> dict[str, Any]:
    return {"id": rid} if rid is not None else {}


def _error(message: str) -> dict[str, str]:
    return {"code": "invalid", "message": message}
