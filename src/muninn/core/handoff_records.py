"""Muninn as the principal side of Principal Handoff 0.5 (contracts/vendor/principal-handoff), beside contracts v1.

In: the projects' records (a task or question raised, amended, withdrawn; a claim that it is done; a note) are checked,
kept once each, and carried out through the same operations a v1 message reaches: the tasks and the projects'
questions. Only the user settles: a project's claim is not evidence independent of the one claiming, so he is asked
(core section 7).

Versions (core section 14): which version a project's records are told in is configured, per project and extension
(`Versions`). A record is checked under the version it came in, kept as it came, and read in 0.5; what Muninn tells a
project that speaks a version before is converted to it.

Out: what Muninn tells the projects is kept in one place, the handoffs; the records a project pulls are read from them,
so a record is the same on every retransmission, and one held back for an undo (or taken back in that time) never
leaves. Each settlement says who decided it and on what authority: the user (principal), the user saying yes to what
Muninn asked (confirmed), or Muninn on evidence (attested). A question asked of a project for the user (asks) is not
part of the specification and stays a v1 handoff. A settlement is bound to the terms it was made against (their
digest), so one is sent only for an item whose need record Muninn holds; any other leaves the v1 way.

What a record says to a project is what that project may hear (core section 13): the handoffs are already written so
(content that came from a deep talk or another project goes back only to where it came from), and another project's
item is named by a uri only Muninn can resolve.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from typing import Any, Protocol, Sequence

from . import ph
from .ports import Clock, HandoffStore, RecordStore, StateStore
from .language import Language

log = logging.getLogger(__name__)

ACTS, DECISIONS = ph.ACTS, ph.DECISIONS  # by name
SPEAKS = "ph:speaks"  # state, until 0.5: by project, the version of the records it last sent
VERSIONS = "ph:versions"  # state: by project ("*": one not named yet), by extension, the version it is told in
URI_SCOPE = "acts/0.4"  # what the uris naming another project's item were first made from: kept, so an item keeps its uri
SETTLES = ("done", "dropped", "already_done", "reopened")  # a task's settlement register, as the handoffs keep it
# how a v1 dismissal was told apart before it was marked `dismissed`: by its fixed opening words, which were Chinese
NO_ANSWER_NEEDED_PREFIX = "這題不用回答"


class Messages(Protocol):
    def receive(self, raw: Sequence[dict[str, Any]]) -> int: ...

    def amend_question(self, qid: str, text: str) -> bool: ...


class Tasks(Protocol):
    def claimed(self, key: str, project: str, outcome: str, why: str, *, inferred: bool = False, claim: str | None = None) -> str: ...

    def withdrawn(self, key: str, project: str, why: str = "", *, replaced: bool = False, replaced_by: str | None = None) -> str: ...

    def replaced(self, key: str, project: str, by: str | None) -> None: ...

    def amended(self, key: str, project: str, text: str, due: str | None) -> str: ...


class Versions:
    """The version each project's requesters speak, by extension: configured (core section 14), not taken from the
    labels of what arrives, since one project's registrar and agents may for a while send different versions. A
    project first heard from is told in the version it spoke then; a record in another version is read all the same
    (Muninn reads every version it implements) and said in the log once, until the configuration is changed."""

    def __init__(self, store: StateStore):
        self._store = store
        self._said: set[tuple[str, str, str]] = set()

    def _all(self) -> dict[str, Any]:
        got = self._store.get_state(VERSIONS)
        if got is None:  # the first time: each project as it was last answered, and a new one as before 0.5
            spoke = self._store.get_state(SPEAKS)
            seed = {p: {ACTS: v, DECISIONS: v} for p, v in {"*": "0.4" if spoke else ph.PH, **(spoke or {})}.items()}
            got = self._store.update_state(VERSIONS, lambda now: now if now is not None else seed)
        return got

    def of(self, project: str, ext: str) -> str:
        """The version `project` is told `ext`'s records in."""
        v = self._all()
        return str((v.get(project) or {}).get(ext) or (v.get("*") or {}).get(ext) or ph.PH)

    def heard(self, project: str, ext: str, version: str) -> None:
        """A record of `ext` came from `project` in `version`."""
        if ext not in (self._all().get(project) or {}):
            self.set(project, ext, version)
        elif (told := self.of(project, ext)) != version and (project, ext, version) not in self._said:
            self._said.add((project, ext, version))
            log.warning("%s sent %s records in %s but is configured to speak %s, and is told in it (muninn handoff-version"
                        " changes it)", project, ext, version, told)

    def set(self, project: str, ext: str, version: str) -> None:
        """`project` speaks `version` of `ext` from now on; "*": every project, those already named and those to come
        (when one counterpart, such as a platform's bus, speaks for them all)."""
        if version not in ph.VERSIONS or ext not in (ACTS, DECISIONS):
            raise ValueError(f"Muninn speaks {', '.join(ph.VERSIONS)} of {ACTS} and {DECISIONS}")
        self._all()
        self._store.update_state(VERSIONS, lambda now: {p: {**v, ext: version} if project in ("*", p) else v
                                                        for p, v in {project: {}, **now}.items()})

    def table(self) -> dict[str, Any]:
        return self._all()


class InStore(RecordStore, StateStore, Protocol):
    """The projects' records, and which version each project speaks."""


class Inbox:
    def __init__(self, store: InStore, clock: Clock, parties: ph.Parties, exts: dict[str, ph.Ext], messages: Messages, tasks: Tasks,
                 versions: Versions, *, words: Language):
        self.words = words
        self._versions = versions
        self._store = store
        self._clock = clock
        self._parties = parties
        self._exts = exts
        self._messages = messages
        self._tasks = tasks

    def receive(self, raw: Sequence[Any]) -> list[dict[str, Any]]:
        """Each record checked on its own (a bad one never blocks the rest): accepted, duplicate, unsupported (an
        extension Muninn does not implement: consumed, not acted on) or invalid, with why."""
        out = []
        for r in raw:
            if not isinstance(r, dict):
                out.append({"id": None, "status": "invalid", "errors": ["not an object"]})
                continue
            spoke, r = r.get("ph"), ph.upgraded(r)  # 0.3 reads as 0.4
            e = self._exts.get(str(r.get("ext")))
            if e is None:
                out.append({"id": r.get("id"), "status": "unsupported"})
                continue
            _, errs = ph.validate(r, e, self._parties)  # under the version it came in
            if r.get("origin") == "principal" or r.get("by") == self._parties.side:  # only Muninn records his settlements
                errs.append("the principal side's records are Muninn's own")
            if errs:
                out.append({"id": r.get("id"), "status": "invalid", "errors": errs})
                continue
            ext = ph.name(r["ext"])
            got = self._store.add_record(ext, r, self._clock.now(), lambda x: ph.canonical(ph.converted(x)))
            if got != "duplicate":
                self._versions.heard(r["project"], ext, str(spoke))
            if got == "new":
                self._apply(ph.converted(r))
            out.append({"id": r["id"], "status": "duplicate" if got == "duplicate" else "accepted"})
        return out

    def _apply(self, r: dict[str, Any]) -> None:
        key, project, terms = r["key"], r["project"], r.get("terms") or {}
        said = str(r.get("note") or "")
        if r["type"] == "note":
            about = r.get("about") or {}
            text = said or str(about.get("quote") or "")
            if text:
                self._messages.receive([{"id": f"note:{r['id']}"[:200], "project": project, "type": "progress", "text": text[:4000]}])
            return
        if r["type"] == "related":  # it changes no item's state (core section 7), but which act replaced another may
            if r["ext"] == ph.label(ACTS) and (r.get("relation") or {}).get("kind") == "replaced_by" and self._replaced(r):
                self._tasks.replaced(key, project, self._replacement(r))
            return
        if r["ext"] == ph.label(ACTS):
            if r["type"] == "raised":
                new = self._messages.receive([{"id": key, "project": project, "type": "task", "text": terms["text"],
                                               **({"due": terms["due"]} if terms.get("due") else {}),
                                               **({"context": r["context"]} if r.get("context") else {}),
                                               **({"new_reason": said} if said else {})}])
                if not new:  # the task came the v1 way before: it reads as the terms its settlement is now bound to
                    self._tasks.amended(key, project, terms["text"], terms.get("due"))
            elif r["type"] == "amended":
                self._tasks.amended(key, project, terms["text"], terms.get("due"))
            elif r["type"] == "withdrawn":
                replaced = r.get("reason") == "replaced"
                self._tasks.withdrawn(key, project, said, replaced=replaced, replaced_by=self._replacement(r) if replaced else None)
            elif r["type"] == "claimed":  # its word alone settles nothing: his standing permission, or he is asked
                basis = str((r.get("evidence") or {}).get("basis") or said or "")
                self._tasks.claimed(key, project, r["outcome"], basis, inferred=r["origin"] == "registrar", claim=r["id"])
            return
        if r["type"] == "raised":
            self._messages.receive([{"id": key, "project": project, "type": "question", "text": terms["text"],
                                     **({"context": r["context"]} if r.get("context") else {})}])
        elif r["type"] == "amended":
            self._messages.amend_question(key, terms["text"])
        elif r["type"] == "withdrawn":  # no longer asked: settled where it was asked, or not needed
            self._messages.receive([{"id": r["id"][:200], "project": project, "type": "drop", "question": key,
                                     "text": said or self.words("handoff_records.withdrawn", reason=r.get("reason"))}])
        elif r["type"] == "claimed":  # he answered it in the project's own session
            answer = r.get("answer") or {}
            self._messages.receive([{"id": r["id"][:200], "project": project, "type": "done", "question": key,
                                     "text": str(answer.get("text") or answer.get("option") or said or self.words("handoff_records.answered"))}])


    def _replaced(self, r: dict[str, Any]) -> bool:
        """The act is withdrawn as replaced: its requester's current need record says so."""
        needs = [ph.converted(x) for x in self._store.records_of(ACTS, r["project"], r["key"])
                 if x.get("type") in ph.NEED and x.get("origin") == "requester"]
        current = max(needs, key=lambda x: (ph.instant(x["ts"]), x["id"]), default=None)
        return bool(current and current["type"] == "withdrawn" and current.get("reason") == "replaced")

    def _replacement(self, r: dict[str, Any]) -> str | None:
        """The key of the act a withdrawn one was replaced by: its requester's single active replaced_by (acts section 2)."""
        rels = [ph.converted(x) for x in self._store.records_of(ACTS, r["project"], r["key"])
                if x.get("type") == "related" and (x.get("relation") or {}).get("kind") == "replaced_by"]
        active = [x["relation"]["target"] for x in rels if x["relation"].get("active", True)
                  and not any(y.get("supersedes") and x["id"] in y["supersedes"] for y in rels)]
        return str(active[0]["key"]) if len(active) == 1 and "key" in active[0] else None


class OutStore(HandoffStore, RecordStore, StateStore, Protocol):
    """The handoffs the records are read from, the projects' records (their terms), and the version each speaks."""


class Outbox:
    def __init__(self, store: OutStore, clock: Clock, parties: ph.Parties, exts: dict[str, ph.Ext], versions: Versions, *, words: Language):
        self.words = words
        self._versions = versions
        self._store = store
        self._clock = clock
        self._parties = parties
        self._exts = exts

    def since(self, project: str, after: int, limit: int = 200) -> tuple[list[dict[str, Any]], int]:
        """The records for `project` told after cursor `after`, and the cursor to ask from next. One held back for an
        undo stops the read there, so it is not passed over; one taken back is never told."""
        now = self._clock.now().isoformat()
        out, cursor = [], after
        for h in self._store.handoffs_to(project, after):
            if h.get("not_before") and h["not_before"] > now and h["status"] != "cancelled":
                break  # held for an undo: told once it comes due
            cursor = h["id"]
            if h["status"] == "cancelled":
                continue
            for r in self.records(h):
                told = self.told(r)
                errs = ph.validate(r, self._exts[r["ext"]], self._parties)[1] or self._invalid_as_told(told)
                if errs:  # never told: a receiver would have to ignore it anyway (core sections 12 and 14)
                    log.warning("handoff %s makes no valid record (%s): not told", h["id"], "; ".join(errs))
                    continue
                out.append(told)
            if len(out) >= limit:
                break
        return out, cursor

    def told(self, r: dict[str, Any]) -> dict[str, Any]:
        """A record Muninn made, as its project is told it: in the version it speaks of that extension."""
        version = self._versions.of(r["project"], ph.name(r["ext"]))
        return r if version == ph.PH else ph.converted(r, version, self._store.records_of(ph.name(r["ext"]), r["project"], r["key"]))

    def _invalid_as_told(self, told: dict[str, Any]) -> list[str]:
        """What makes it no record in the version it is told in (0.3: in the 0.4 it reads as)."""
        r = ph.upgraded(told)
        return ph.validate(r, self._exts[r["ext"]], self._parties)[1]

    def ack(self, project: str, through: int) -> int:
        """The project has the records up to cursor `through`: what they stand for has left (an undo after this is told
        as a new record, not taken back). A handoff that stands for no record (a question asked for the user) still
        leaves the v1 way, so it stays pending. The cursor kept."""
        for h in self._store.handoffs_to(project):
            if h["id"] <= through and h["status"] == "pending" and not (h.get("not_before") and h["not_before"] > self._clock.now().isoformat()) \
                    and self.records(h):
                self._store.set_handoff_status(h["id"], "delivered", self._clock.now())
        return through

    def records(self, h: dict[str, Any]) -> list[dict[str, Any]]:
        """The records one handoff stands for (none for a question asked for the user, which stays v1)."""
        effect, project, hid = h.get("effect"), str(h["project"]), h["id"]
        key = str(h.get("reply_to") or "")
        at = ph.instant(str(h["ts"])) if h.get("ts") else None
        when = at or self._clock.now()
        base = {"ph": ph.PH, "origin": "principal", "by": self._parties.side, "project": project, "ts": ph.stamp(when)}
        if effect in ("done", "dropped", "already_done") and key:
            need = self._need(ACTS, project, key)
            if need is None:
                return []  # a task that came the v1 way: its settlement goes back the same way
            authority, evidence, adopts = self._authority(h)
            out = [{**base, "ext": ph.label(ACTS), "id": f"s:{key}:h{hid}", "type": "settled", "key": key, "outcome": effect,
                    "authority": authority, "terms_rev": need["terms_rev"], "terms_digest": ph.terms_digest(need),
                    **({"evidence": evidence} if evidence else {}), **({"adopts": adopts} if adopts else {}),
                    **self._supersedes(project, key, hid), **({"note": h["why"]} if h.get("why") else {})}]
            if effect == "already_done" and h.get("same_as"):  # a microsecond after: Muninn's times only ever rise
                out.append(self._related({**base, "ts": ph.stamp(when + timedelta(microseconds=1))}, key, hid, h["same_as"]))
            return out
        if effect == "reopened" and key:
            return [{**base, "ext": ph.label(ACTS), "id": f"o:{key}:h{hid}", "type": "reopened", "key": key,
                     "restore_text": str(h.get("item") or h.get("fact") or ""), **self._supersedes(project, key, hid)}]
        if effect == "answers" and key:
            need = self._need(DECISIONS, project, key)
            if need is None:
                return []
            dismissed = bool(h.get("dismissed")) or str(h.get("fact") or "").startswith(NO_ANSWER_NEEDED_PREFIX)
            return [{**base, "ext": ph.label(DECISIONS), "id": f"s:{key}:h{hid}", "type": "settled", "key": key,
                     "outcome": "dismissed" if dismissed else "decided",
                     **({"note": str(h.get("why") or self.words("handoff_records.dismissed_note"))} if dismissed else {"answer": {"text": str(h.get("fact") or "")}}),
                     "authority": {"kind": "principal", "decider": self._parties.principal},
                     "terms_rev": need["terms_rev"], "terms_digest": ph.terms_digest(need), **self._supersedes(project, key, hid)}]
        if effect == "same_as" and key:
            return [self._related(base, key, hid, h["target"])]
        if effect == "premise" and key:
            return [{**base, "ext": ph.label(ACTS), "id": f"n:{key}:h{hid}", "type": "note", "key": key, "about": {"key": key},
                     "note": str(h.get("fact") or "")}]
        if effect in ("resolves", "changes", "relevant"):  # about a to-do the project never keyed: its own words
            said = " ".join(x for x in (str(h.get("fact") or ""), str(h.get("why") or "")) if x)
            return [{**base, "ext": ph.label(ACTS), "id": f"n:h{hid}", "type": "note", "key": f"muninn-note-h{hid}",
                     "about": {"quote": str(h.get("item") or h.get("fact") or "")}, "note": said}]
        return []

    def _related(self, base: dict[str, Any], key: str, hid: int, target: dict[str, str]) -> dict[str, Any]:
        """Another project's item is named by a uri only Muninn resolves (core section 13): never its project or key."""
        if "project" not in target:
            to: dict[str, str] = {"uri": target["uri"]}
        elif target["project"] == base["project"]:
            to = {"ext": ph.label(ACTS), "project": target["project"], "key": target["key"]}
        else:
            to = {"uri": "muninn:item:" + hashlib.sha256(f"{URI_SCOPE}\n{target['project']}\n{target['key']}".encode()).hexdigest()[:24]}
        return {**base, "ext": ph.label(ACTS), "id": f"rel:{key}:h{hid}", "type": "related", "key": key,
                "relation": {"kind": "same_as", "target": to, "active": True}}

    def _authority(self, h: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        """Who decided a task's settlement, and on what: the user himself; the user saying yes to what Muninn asked;
        or Muninn, on evidence (his words read as settling it, his standing rule, a project's claim it adopts)."""
        decided = h.get("decided") or {}
        by, why = str(decided.get("by") or ""), str(decided.get("why") or h.get("why") or "")
        principal = self._parties.principal
        claim = [str(decided["claim"])] if decided.get("claim") else []
        if decided.get("confirmed"):  # his yes to a project's claim adopts it
            return {"kind": "confirmed", "decider": principal}, {"proposal": f"muninn:question:{decided['confirmed']}"}, claim
        if by == "user":
            return {"kind": "principal", "decider": principal}, {}, []
        if decided.get("mandate"):  # under his standing permission (core section 5.1): no claim is adopted by it
            side = self._parties.side
            return {"kind": "delegated", "decider": side, "under": f"muninn:mandate:{decided['mandate']}", "chain": [principal, side]}, {}, []
        basis = self.words("handoff_records.said_by", by=by, why=why) if by not in ("", "muninn", "evidence") \
            else (why or self.words("handoff_records.muninn_judged"))
        return {"kind": "attested", "decider": self._parties.side}, {"basis": basis}, claim

    def _need(self, ext: str, project: str, key: str) -> dict[str, Any] | None:
        """The need record the settlement is made against: the latest terms the project sent (None: it sent none)."""
        held = [ph.converted(r) for r in self._store.records_of(ext, project, key)
                if r.get("type") in ("raised", "amended") and r.get("origin") == "requester"]
        return max(held, key=lambda r: (r.get("terms_rev", 0), ph.instant(r["ts"])), default=None)

    def _supersedes(self, project: str, key: str, hid: int) -> dict[str, Any]:
        """The settlement this one replaces: Muninn's last earlier one for the item, if any (core section 6)."""
        earlier = [h for h in self._store.handoffs_to(project, key=key)
                   if h["id"] < hid and h.get("effect") in SETTLES + ("answers",) and h["status"] != "cancelled"]
        if not earlier:
            return {}
        last = earlier[-1]
        prefix = "o" if last.get("effect") == "reopened" else "s"
        return {"supersedes": [f"{prefix}:{key}:h{last['id']}"]}


def parties_from(principal: str | None, owner_label: str, side: str = "muninn") -> ph.Parties:
    """The configured principal identifier, or one made from the owner's label; never Muninn's own (core R6)."""
    who = principal or f"person:{owner_label.strip().lower() or 'principal'}"
    if who == side:
        raise ValueError("the principal identifier cannot be the principal side's own")
    return ph.Parties(who, side)

