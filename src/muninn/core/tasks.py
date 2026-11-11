"""What only the user can do, kept once.

A task is born where it is noticed: a project posts it to the mailbox (its key
is that message's id, so the project knows it at once and writes it next to
the line in its own notes), or Muninn makes one itself (`muninn:<n>`). Every
later message refers to the same key. Muninn shows a short number (T-12).

- Arriving tasks are stored at once; a pass at each tick compares the new
  ones with the rest in one model call. The same task from another project
  becomes a second origin of the open task, not a duplicate; closing it
  tells every origin. A project re-sending what the user finished in the
  last two weeks gets "already done" back, unless it said why it is new
  (new_reason): the sender decides, never the user.
- A done that arrives before its task leaves a closed record, so the task is
  born closed.

What the user says is the decision: closing, dropping, merging all happen at
once and are undoable (an ActionRecord with the undo).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable, Collection, Protocol, Sequence

from .actions import ActionGate, DecidedBy, Ran, TaskClose, TaskDue
from .handoffs import Handoffs
from .models import ActionRecord, Confirmation
from .ports import ActionLog, Clock, ConfirmationStore, StateStore, TaskStore
from .language import Language

RECENT = timedelta(days=14)
BATCH = 20  # new tasks per matching call: a migration brings dozens at once
FRESH = "new"  # a link marking a task its project said is new (new_reason): never matched away
# (new tasks, candidate tasks) → new key → key of the candidate that is the same task;
# None when there is no trustworthy answer (no reply, or only a fallback model's)
MatchTasks = Callable[[Sequence[dict[str, Any]], Sequence[dict[str, Any]]], "dict[str, str] | None"]


class TaskStorage(TaskStore, ActionLog, StateStore, ConfirmationStore, Protocol):
    """The tasks, the merge actions logged for undo, the matching cursor, and what the user is asked about them."""

REPLACED = "replaced"  # the question about an act its project raised again under another key (acts section 2)
KEPT = "replaced:kept"  # a link marking a replaced act the user kept as a to-do
# state: the user's standing permission for Muninn to take a project's own word that a task is done (or will not be),
# instead of asking each time: {id, granted, words, revoked}. Kept after a revocation, so what was settled under it
# can still name it. A registrar's reading of a line (gone, ticked) is never covered: what happened to a line is not
# the project saying the work is done.
CLAIMS_MANDATE = "mandate:project-claims"


class TaskRegistry:
    def __init__(self, store: TaskStorage, clock: Clock, handoffs: Handoffs, match: MatchTasks, gate: ActionGate,
                 on_close: Callable[[dict[str, Any]], None] | None = None, *, words: Language):
        self.words = words
        self._store = store
        self._clock = clock
        self._handoffs = handoffs
        self._match = match
        self._gate = gate
        self._on_close = on_close  # e.g. marks a linked plan deadline done

    # ── reading ─────────────────────────────────────────────────────────────

    def open(self, project: str | None = None) -> list[dict[str, Any]]:
        return self._store.tasks(status="open", project=project)

    def get(self, key: str) -> dict[str, Any] | None:
        return self._store.task(key)

    # ── from the projects ───────────────────────────────────────────────────

    def receive(self, m: dict[str, Any]) -> str:
        """A parsed mailbox message of type task, done or drop (a project raising a task, saying it is done, no longer
        needing it). Returns what happened."""
        if m["type"] == "task":
            return self._intake(m)
        if m["type"] == "drop":
            return self.withdrawn(m["task"], m["project"], m["text"])
        return self.claimed(m["task"], m["project"], "done", m["text"], inferred=bool(m.get("inferred")))

    def claimed(self, key: str, project: str, outcome: str, why: str, *, inferred: bool = False, claim: str | None = None) -> str:
        """A project says the task is done (or will not be done): only the user settles it, so Muninn takes the claim
        as its evidence (undoably), or asks first when the sender only inferred it. `claim`: the claim's own id."""
        status = "done" if outcome == "done" else "dropped"
        t = self._store.task(key)
        # a replaced act is settled only by the user himself (acts section 2): never under the standing permission
        mandate = None if inferred or any(x.startswith("replaced") for x in (t or {}).get("links", [])) else self.claims_mandate()
        if t is None and inferred:
            return "unknown task"  # someone's guess that a task we never had is done: nothing to ask about
        if t is None:
            if not _message_key(key):
                return "unknown task"  # a short number or a Muninn key that does not exist: nothing to wait for
            # the claim came first: the task will be born closed
            self._store.add_task(key, {"title": why, "status": status, "closed_by": project}, self._clock.now())
            return "closed before it arrived"
        if t["status"] != "open":
            return status
        # the project's word alone settles nothing: under his standing permission it is taken (undoably); else he is asked
        self.close(t["key"], status, by=project, why=why, inferred=mandate is None, claims=[(key, claim)] if claim else (),
                   mandate=mandate["id"] if mandate else None)
        return status

    def claims_mandate(self) -> dict[str, Any] | None:
        """The user's standing permission to take a project's word that a task is done, while it stands."""
        m = self._store.get_state(CLAIMS_MANDATE)
        return m if m and not m.get("revoked") else None

    def grant_claims_mandate(self, words: str) -> dict[str, Any]:
        """The user gave it (`words`: what he said, kept as the permission's own text)."""
        now = self._clock.now()
        m = {"id": f"project-claims-{now:%Y%m%d}", "granted": now.isoformat(), "words": words, "revoked": None}
        self._store.set_state(CLAIMS_MANDATE, m)
        return m

    def revoke_claims_mandate(self) -> bool:
        m = self._store.get_state(CLAIMS_MANDATE)
        if not m or m.get("revoked"):
            return False
        self._store.set_state(CLAIMS_MANDATE, {**m, "revoked": self._clock.now().isoformat()})
        return True

    def withdrawn(self, key: str, project: str, why: str = "", *, replaced: bool = False, replaced_by: str | None = None) -> str:
        """A project no longer needs the task. Others that still do keep it as it was; when no one else does, it may
        still be something the user promised: it stays open, and the user is asked whether to drop it. One the project
        raised again under another key (`replaced`; `replaced_by`: that key, when there is a single one) is asked about
        as `replaced` says."""
        t = self._store.task(key)
        if t is None or t["status"] != "open":
            return "nothing open"
        for old in self.open():  # an act it replaced is asked about on its own now
            if f"replaced_by:{t['key']}" in old["links"]:
                self.replaced(old["key"], project, t["key"], ended=True)
        if any(o["project"] != project for o in t["origins"]):
            for o in t["origins"]:
                if o["project"] == project:
                    self._store.remove_task_origin(t["key"], o["ref"])
            return "origin withdrawn"
        if replaced:
            self.replaced(t["key"], project, replaced_by)
            return "replaced"
        self.close(t["key"], "dropped", by=project, why=why or self.words("tasks.not_needed_by", project=project), inferred=True)
        return "asked"

    def replaced(self, key: str, project: str, by: str | None, *, ended: bool = False) -> None:
        """The act `key`, withdrawn as replaced, has `by` as its single replacement (None: none, or more than one).
        While that one is open the user is asked about the two together; once it is settled or withdrawn, or is no
        longer the single replacement, about this one on its own. Either way nothing is chosen for him, and only he
        settles it, never the replacement's settlement (acts section 2). `ended`: its project withdrew the replacement."""
        t = self._store.task(key)
        if t is None or t["status"] != "open":
            return
        new = self._store.task(by) if by else None
        beside = new if new is not None and new["status"] == "open" and not ended else None
        links = [x for x in t["links"] if not x.startswith("replaced")] + [f"replaced_by:{beside['key']}" if beside else "replaced"]
        if links != t["links"]:
            t = self._store.update_task(t["key"], self._clock.now(), links=links)
        self._ask_about_replaced(t, beside, new)

    def amended(self, key: str, project: str, text: str, due: str | None) -> str:
        """A project changed what it asks: the task's words and date are the new ones. When it was merged into
        another task, only what that project asked is changed there: the task's own words are another item's."""
        t = self._store.task(key)
        if t is None:
            return "unknown task"
        for o in t["origins"]:
            if o["project"] == project and o["ref"] == key and o["text"] != text:
                self._store.remove_task_origin(t["key"], o["ref"])
                self._store.add_task_origin(t["key"], project, key, text)
        if t["key"] == key and (t["title"] != text or (due and _due(due) != t["due"])):
            self._store.update_task(t["key"], self._clock.now(), title=text, **({"due": _due(due)} if due else {}))
        return "amended"

    def _intake(self, m: dict[str, Any]) -> str:
        """Stores the task at once (no model on the way in: a migration posts
        dozens together); sort_new() looks for duplicates afterwards."""
        key, project, text = m["id"], m["project"], m["text"]
        existing = self._store.task(key)
        if existing is not None:
            if not existing["origins"]:  # a closed record waiting for its task
                self._store.update_task(existing["key"], self._clock.now(), title=text, due=_due(m.get("due")), context=m.get("context"))
                self._store.add_task_origin(existing["key"], project, key, text)
            return "known"
        links = [FRESH] if m.get("new_reason") else []
        self._store.add_task(key, {"title": text, "due": _due(m.get("due")), "context": m.get("new_reason") or m.get("context"),
                                   "links": links}, self._clock.now())
        self._store.add_task_origin(key, project, key, text)
        return "created"

    def sort_new(self) -> int:
        """Tasks that arrived since the last pass, BATCH at a time, against
        the open ones (Muninn's own too), those finished or dropped in the
        last two weeks, and each other: the same task still open becomes
        another origin of it; the same task the user already settled goes
        back as already done, to whichever project sent it. Without a
        trustworthy answer the pass stops where it is and tries again next
        tick."""
        n = 0
        while True:
            seen = int(self._store.get_state("tasks:sorted_n") or 0)
            batch = sorted((t for t in self._store.tasks(status="open") if t["n"] > seen and t["origins"]),
                           key=lambda t: t["n"])[:BATCH]
            if not batch:
                return n
            new = [t for t in batch if FRESH not in t["links"]]  # a project said why it is new: not matched away
            if not new:
                self._store.set_state("tasks:sorted_n", batch[-1]["n"])
                continue
            since = (self._clock.now() - RECENT).isoformat()
            new_keys = {t["key"] for t in new}
            old = [t for t in self._store.tasks(status=None) if t["key"] not in new_keys
                   and (t["status"] == "open" or (t["status"] in ("done", "dropped") and (t["closed"] or "") >= since))]
            same = self._match([_brief(t) for t in new], [_brief(t) for t in old + new])
            if same is None:
                return n
            by_key = {t["key"]: t for t in old + new}
            order = {t["key"]: t["n"] for t in new}
            for t in new:
                other = by_key.get(same.get(t["key"], ""))
                if other is None or other["key"] == t["key"] or order.get(other["key"], 0) > t["n"]:
                    continue
                t, other = self._store.task(t["key"]), self._store.task(other["key"])  # as they are now
                if t is None or other is None or t["status"] != "open":
                    continue
                if other["status"] == "open":
                    self._merge(t, other)
                else:
                    self._already_done(t, other)
                n += 1
            self._store.set_state("tasks:sorted_n", batch[-1]["n"])

    def _merge(self, t: dict[str, Any], into: dict[str, Any]) -> None:
        for o in t["origins"]:
            self._store.add_task_origin(into["key"], o["project"], o["ref"], o["text"])
        links = list(dict.fromkeys([*into["links"], *(x for x in t["links"] if x != FRESH)]))
        context = "\n".join(x for x in dict.fromkeys([into["context"], t["context"]]) if x) or None
        self._store.update_task(into["key"], self._clock.now(), due=into["due"] or t["due"], links=links, context=context)
        self._store.delete_task(t["key"])
        same = next(iter(into["origins"]), None)  # what each project's item is now taken as one with
        for o in t["origins"]:
            self._handoffs.same_as(o["project"], o["ref"], {"project": same["project"], "key": same["ref"]} if same
                                   else {"uri": f"muninn:task:{into['key']}"})
        self._store.log_action(ActionRecord(None, self._clock.now(), "", self.words("tasks.merged", title=t["title"][:40], n=into["n"]),
                                            {"kind": "task_merge", "key": into["key"], "refs": [o["ref"] for o in t["origins"]],
                                             "task": {k: t[k] for k in ("title", "due", "links", "context")},
                                             "into": {k: into[k] for k in ("due", "links", "context")}}))

    def _already_done(self, t: dict[str, Any], done: dict[str, Any]) -> None:
        self._store.update_task(t["key"], self._clock.now(), status="done", closed_by="already_done", links=[f"task:{done['key']}"])
        for o in t["origins"]:
            same = next(iter(done["origins"]), None)
            self._handoffs.add(project=o["project"], item=o["text"] or t["title"], fact=self.words("tasks.already_settled", day=(done["closed"] or "")[:10], status=done["status"], n=done["n"]),
                               effect="already_done", reply_to=o["ref"], task=done["key"],
                               why=self.words("tasks.already_settled_why"), decided={"by": "muninn"},
                               same_as={"project": same["project"], "key": same["ref"]} if same else {"uri": f"muninn:task:{done['key']}"})

    # ── by Muninn and the user ──────────────────────────────────────────────

    def add(self, title: str, due: str | None = None, links: Sequence[str] = (), context: str | None = None) -> dict[str, Any]:
        """A task Muninn itself keeps (the user asked for it, or it links a plan item)."""
        n = len(self._store.tasks(status=None)) + 1
        while self._store.task(f"muninn:{n}") is not None:
            n += 1
        t, _ = self._store.add_task(f"muninn:{n}", {"title": title, "due": _due(due), "links": list(links), "context": context},
                                    self._clock.now())
        return t

    def close(self, key: str, status: str = "done", *, by: str = "user", why: str = "",
              only_to: Collection[str] | None = None, inferred: bool = False, claims: Sequence[tuple[str, str]] = (),
              mandate: str | None = None) -> Ran | None:
        """Done or dropped, through the gate (which decides whether the
        projects hear of it at once or later). `only_to`: the why came from a
        lent session's content, so only those projects hear it. `inferred`:
        `by` only guessed it is done, so the user is asked first. `claims`: (the project's key for its item, its
        claim) it settles on; `mandate`: the user's standing permission it is settled under. Undoable."""
        limit = tuple(sorted(only_to)) if only_to is not None else None
        return self._gate.run(TaskClose(key, status, why, by, limit, mandate=mandate, claims=tuple(claims)),
                              DecidedBy.INFERRED_UNSURE if inferred else decided_by(by))

    def set_due(self, key: str, due: str) -> Ran | None:
        return self._gate.run(TaskDue(key, due), DecidedBy.USER) if _due(due) else None

    def change_status(self, key: str, status: str, *, by: str, why: str, hold: timedelta | None,
                      only_to: Collection[str] | None = None, confirmed: int | None = None,
                      claims: Sequence[tuple[str, str]] = (), mandate: str | None = None) -> tuple[dict[str, Any], str] | None:
        """The change itself (the gate logs it): every origin hears of it, with who decided it (`by`; `confirmed`:
        the question the user said yes to; `claims`: each item's claim it settles on, as `claim_for` finds them), the one that claimed it too
        (only the user settles: its claim is not the settlement), after `hold` when set. (task, previous status)."""
        t = self._store.task(key)
        if t is None or t["status"] == status:
            return None
        prev = t["status"]
        t = self._store.update_task(t["key"], self._clock.now(), status=status, closed_by=by, why=why)
        decided = {"by": by, **({"confirmed": confirmed} if confirmed else {}),
                   **({"mandate": mandate} if mandate and not confirmed else {})}
        self._tell(t, "done" if status == "done" else "dropped", why, hold=hold, only_to=only_to, decided=decided,
                   claims=claims)
        if self._on_close and status == "done":
            self._on_close(t)
        for old in self.open():  # the act it replaced is now asked about on its own
            if f"replaced_by:{t['key']}" in old["links"]:
                self.replaced(old["key"], "", t["key"])
        return t, prev

    def _ask_about_replaced(self, old: dict[str, Any], beside: dict[str, Any] | None, new: dict[str, Any] | None) -> None:
        """The question about a replaced act: with its replacement while that is open (`beside`), else on its own
        (`new`: the replacement, if Muninn has it, for what became of it). An earlier one about it is taken back."""
        w, data = self.words, {"task": old["key"], "by": (beside or new or {}).get("key"), "neutral": True, "together": beside is not None}
        for c in self._store.confirmations():
            if c.kind == REPLACED and c.data.get("task") == old["key"]:
                if c.data == data:
                    return
                self._store.answer_confirmation(c.id, "", self._clock.now())  # no longer the question
        if beside is not None:
            question = w("tasks.replaced_together", old=old["title"], new=beside["title"])
            options = (w("tasks.replaced_answer_same"), w("tasks.replaced_answer_done"), w("tasks.replaced_answer_keep"))
        else:
            how = w("tasks.replaced_withdrawn") if new is not None and new["status"] == "open" else w(f"tasks.replaced_{new['status']}") \
                if new is not None and new["status"] in ("done", "dropped") else None
            question = w("tasks.replaced_asked", old=old["title"], new=new["title"], how=how) if how else w("tasks.replaced_alone", old=old["title"])
            options = (w("tasks.replaced_answer_done"), w("tasks.replaced_answer_dropped"), w("tasks.replaced_answer_keep"))
        self._store.add_confirmation(Confirmation(None, self._clock.now(), REPLACED, question, options, data, key=f"{REPLACED}:{old['key']}"))

    def replaced_answered(self, c: Confirmation, answer: str) -> None:
        """The user's own settlement of a replaced act, or keeping it as a to-do of his own (then it is not asked about
        again)."""
        w = self.words
        status = {w("tasks.replaced_answer_done"): "done", w("tasks.replaced_answer_dropped"): "dropped",
                  w("tasks.replaced_answer_same"): "dropped"}.get(answer)
        if status is not None:
            new = self._store.task(str(c.data.get("by") or ""))
            same = answer == w("tasks.replaced_answer_same") and new is not None
            self.close(str(c.data["task"]), status, by="user", why=w("tasks.replaced_same", new=new["title"]) if same else "")
        elif (t := self._store.task(str(c.data["task"]))) is not None:  # his own to-do now: still settled only by him
            self._store.update_task(t["key"], self._clock.now(), links=[x for x in t["links"] if not x.startswith("replaced")] + [KEPT])

    def change_due(self, key: str, due: str) -> tuple[dict[str, Any], str | None] | None:
        """The user's own date for it: the projects that asked hear the premise changed (what they ask is theirs to
        change)."""
        t = self._store.task(key)
        if t is None or _due(due) is None:
            return None
        for o in t["origins"]:
            self._handoffs.premise(o["project"], o["ref"], self.words("tasks.due_moved", due=_due(due)))
        return self._store.update_task(t["key"], self._clock.now(), due=_due(due)), t["due"]

    def undo(self, undo: dict[str, Any]) -> bool:
        if undo["kind"] == "task_due":
            return self._store.update_task(undo["key"], self._clock.now(), due=undo["prev"]) is not None
        if undo["kind"] == "task_status":
            t = self._store.update_task(undo["key"], self._clock.now(), status=undo["prev"], closed_by=None, why=None)
            if t is not None and undo["prev"] == "open":
                self._tell(t, "reopened", self.words("tasks.reopened"))
            return t is not None
        if undo["kind"] == "task_merge":  # split the merged origins back out into a task of their own
            t = self._store.task(undo["key"])
            origins = [o for o in (t or {}).get("origins", []) if o["ref"] in undo["refs"]]
            if not origins:
                return False
            first = origins[0]
            for o in origins:
                self._store.remove_task_origin(undo["key"], o["ref"])
            was = undo.get("task") or {}
            self._store.add_task(first["ref"], {"title": was.get("title") or first["text"] or t["title"], "due": was.get("due"),
                                                "context": was.get("context"), "links": [*(was.get("links") or []), FRESH]},
                                 self._clock.now())
            for o in origins:
                self._store.add_task_origin(first["ref"], o["project"], o["ref"], o["text"])
            if "into" in undo:
                self._store.update_task(undo["key"], self._clock.now(), **undo["into"])
            return True
        return False

    def _tell(self, t: dict[str, Any], effect: str, why: str, hold: timedelta | None = None,
              only_to: Collection[str] | None = None, decided: dict[str, Any] | None = None,
              claims: Sequence[tuple[str, str]] = ()) -> None:
        pending = {h.get("reply_to"): h for h in self._handoffs.pending(include_held=True) if h.get("effect") in ("done", "dropped")}
        extra = {"not_before": (self._clock.now() + hold).isoformat()} if hold else {}
        for o in t["origins"]:
            if effect == "reopened" and o["ref"] in pending:  # the close never left: withdraw it instead
                self._handoffs.set_status(pending[o["ref"]]["id"], "cancelled")
                continue
            said = why if only_to is None or o["project"] in only_to else ""  # outside it: that it happened, not why
            # the project that claimed it hears the settlement as a record only (v1 never echoed its own report)
            tell = self._handoffs.recorded if decided and decided.get("by") == o["project"] else self._handoffs.add
            claim = claim_for(o["ref"], claims, len(t["origins"]))
            tell(project=o["project"], item=o["text"] or t["title"], fact=said or t["title"],
                               quote=said or None, effect=effect, reply_to=o["ref"], why=said,
                               **({"decided": {**decided, **({"claim": claim} if claim else {}), "why": said}} if decided else {}), **extra)


def claim_for(ref: str, claims: Sequence[tuple[str, str]], origins: int) -> str | None:
    """The claim one item's settlement adopts: the one its project made about that item, never another item's. One
    kept without its item (from before claims were kept by item) goes with the item its id names, or with the task's
    only item."""
    loose = [c for r, c in claims if not r]
    return (next((c for r, c in claims if r == ref), None)
            or next((c for c in loose if f":{ref}:" in c), None)
            or (loose[0] if loose and origins == 1 else None))


def _due(value: Any) -> str | None:
    try:
        return date.fromisoformat(str(value)).isoformat() if value else None
    except ValueError:
        return None


def _brief(t: dict[str, Any]) -> dict[str, Any]:
    return {"key": t["key"], "text": t["title"], "projects": sorted({o["project"] for o in t["origins"]}), "status": t["status"]}


def _message_key(key: str) -> bool:
    """A project message id (which a task may still arrive under), not a short number or a Muninn key."""
    return not (key.upper().startswith("T-") and key[2:].isdigit()) and not key.startswith("muninn:")


def decided_by(by: str) -> DecidedBy:
    """Who closed it: the user (or Muninn following the user's own deadline), Muninn's evidence, or a project."""
    return {"user": DecidedBy.USER, "muninn": DecidedBy.USER, "evidence": DecidedBy.INFERRED_SURE}.get(by, DecidedBy.AGENT)


class TaskActor:
    """The tasks as an actor: a task as the topic of a discussion, and the tools to add, close and redate one."""

    kinds = ("task",)

    def __init__(self, tasks: TaskRegistry, project_domain: Callable[[str], str | None]):
        """`project_domain`: the domain a project's activity is attributed to (the task's domain is its first known one)."""
        self._tasks = tasks
        self._project_domain = project_domain

    def topic(self, kind: str, ref: str) -> dict[str, Any] | None:
        t = self._tasks.get(ref)
        if t is None:
            return None
        projects = [o["project"] for o in t["origins"]]
        domain = next((d for d in (self._project_domain(p) for p in projects) if d), None)
        return {"kind": "task", "ref": t["key"], "domain": domain, "task": f"T-{t['n']} {t['title']}", "due": t["due"],
                "context": t["context"], "projects": projects,
                "note": "This is something only the user can do. When the user says it is done, not needed, or due on another date, use close_task / set_task_due."}

    def tools(self) -> list[Any]:
        """The tasks' tools: add one, close it, move its date."""
        from .tools.tasks import AddTask, CloseTask, SetTaskDue

        return [AddTask(self._tasks), CloseTask(self._tasks), SetTaskDue(self._tasks)]
