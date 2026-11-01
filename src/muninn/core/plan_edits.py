"""Changes to a domain's plan that the user asked for and confirmed.

Each edit works on the plan document and tracker rows as plain mappings (the
YAML adapter passes its round-trip objects, so comments survive), returns the
edit that undoes it, and describes itself for the confirmation question.
New kinds of change are new classes registered in EDITS.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Callable, ClassVar, MutableMapping, MutableSequence, Protocol

from .language import Language


class Refused(ValueError):
    """An edit that does not fit the plan. It is applied where no language is known (the plans adapter), so it
    carries how to say why: `say(words)`, in the language whoever tells the user speaks; its message is the
    plain technical reason, for logs."""

    def __init__(self, reason: str, say: Callable[[Language], str]):
        super().__init__(reason)
        self.say = say


class PlanEdit(Protocol):
    op: ClassVar[str]

    def apply(self, doc: MutableMapping[str, Any], rows: MutableSequence[Any]) -> PlanEdit: ...

    def describe(self, doc: MutableMapping[str, Any], words: Language) -> str: ...


def _find(items: Any, key: str, value: str) -> Any:
    return next((x for x in items or [] if str(x.get(key)) == value), None)


def _title(doc: MutableMapping[str, Any], kind: str, ident: str) -> str:
    x = _find(doc.get(kind), "id", ident)
    return str(x.get("title") or (f"{x.get('company', '')} {x.get('role', '')}".strip()) or ident) if x else ident


@dataclass(frozen=True)
class SetGoalTarget:
    goal: str
    target: float
    op: ClassVar[str] = "goal_target"

    def apply(self, doc, rows):
        g = _find(doc.get("goals"), "id", self.goal)
        if g is None:
            raise Refused(f"no goal {self.goal}", lambda words: words("plan_edits.no_goal", goal=self.goal))
        old = g.get("target")
        g["target"] = int(self.target) if float(self.target).is_integer() else float(self.target)
        return SetGoalTarget(self.goal, float(old))

    def describe(self, doc, words):
        g = _find(doc.get("goals"), "id", self.goal) or {}
        return words("plan_edits.goal_target", title=_title(doc, "goals", self.goal), period=g.get("period", "week"),
                     target=self.target, now=g.get("target"))


@dataclass(frozen=True)
class SetDeadlineDue:
    deadline: str
    due: str  # YYYY-MM-DD
    op: ClassVar[str] = "deadline_due"

    def apply(self, doc, rows):
        d = _find(doc.get("deadlines"), "id", self.deadline)
        if d is None:
            raise Refused(f"no deadline {self.deadline}", lambda words: words("plan_edits.no_deadline", deadline=self.deadline))
        date.fromisoformat(self.due)
        old = str(d.get("due"))
        d["due"] = date.fromisoformat(self.due)
        return SetDeadlineDue(self.deadline, old)

    def describe(self, doc, words):
        d = _find(doc.get("deadlines"), "id", self.deadline) or {}
        return words("plan_edits.deadline_due", title=_title(doc, "deadlines", self.deadline), due=self.due, now=d.get("due"))


@dataclass(frozen=True)
class AddTarget:
    id: str
    company: str
    role: str
    priority: int = 2
    url: str | None = None
    next_action: str = "投遞"
    next_action_due: str | None = None
    op: ClassVar[str] = "add_target"

    def apply(self, doc, rows):
        if _find(doc.get("targets"), "id", self.id) or _find(rows, "target", self.id):
            raise Refused(f"already there: {self.id}", lambda words: words("plan_edits.already_there", id=self.id))
        target = {"id": self.id, "company": self.company, "role": self.role, "priority": int(self.priority)}
        if self.url:
            target["url"] = self.url
        doc.setdefault("targets", []).append(target)
        row = {"target": self.id, "status": "todo", "next_action": self.next_action}
        if self.next_action_due:
            row["next_action_due"] = date.fromisoformat(self.next_action_due)
        rows.append(row)
        return RemoveTarget(self.id)

    def describe(self, doc, words):
        return words("plan_edits.add_target", company=self.company, role=self.role, priority=self.priority)


@dataclass(frozen=True)
class RemoveTarget:
    id: str
    op: ClassVar[str] = "remove_target"

    def apply(self, doc, rows):
        t = _find(doc.get("targets"), "id", self.id)
        row = _find(rows, "target", self.id)
        if t is None and row is None:
            raise Refused(f"not there: {self.id}", lambda words: words("plan_edits.not_there", id=self.id))
        if t is not None:
            doc["targets"].remove(t)
        if row is not None:
            rows.remove(row)
        return RestoreTarget(_plain(t), _plain(row))

    def describe(self, doc, words):
        return words("plan_edits.remove_target", title=_title(doc, "targets", self.id))


@dataclass(frozen=True)
class RestoreTarget:
    """The undo of a removal: puts back exactly what was there."""

    target: dict[str, Any] | None = None
    row: dict[str, Any] | None = None
    op: ClassVar[str] = "restore_target"

    def apply(self, doc, rows):
        ident = (self.target or self.row or {}).get("id") or (self.row or {}).get("target")
        if self.target:
            doc.setdefault("targets", []).append(dict(self.target))
        if self.row:
            rows.append({k: (date.fromisoformat(v) if k in ("applied", "next_action_due") and isinstance(v, str) else v)
                         for k, v in self.row.items()})
        return RemoveTarget(str(ident))

    def describe(self, doc, words):
        return words("plan_edits.restore_target")


@dataclass(frozen=True)
class RemoveEntry:
    """Drops a goal or a deadline the user does not want."""

    section: str  # goals | deadlines
    id: str
    op: ClassVar[str] = "remove_entry"

    def apply(self, doc, rows):
        if self.section not in ("goals", "deadlines"):
            raise Refused(f"cannot remove from {self.section}", lambda words: words("plan_edits.cannot_remove", section=self.section))
        items = doc.get(self.section) or []
        x = _find(items, "id", self.id)
        if x is None:
            raise Refused(f"not there: {self.id}", lambda words: words("plan_edits.not_there", id=self.id))
        index = list(items).index(x)
        items.remove(x)
        return RestoreEntry(self.section, _plain(x), index)

    def describe(self, doc, words):
        return words("plan_edits.remove_entry", title=_title(doc, self.section, self.id))


@dataclass(frozen=True)
class RestoreEntry:
    section: str
    entry: dict[str, Any]
    index: int = -1
    op: ClassVar[str] = "restore_entry"

    def apply(self, doc, rows):
        items = doc.setdefault(self.section, [])
        entry = {k: (date.fromisoformat(v) if k == "due" and isinstance(v, str) else v) for k, v in self.entry.items()}
        if 0 <= self.index <= len(items):
            items.insert(self.index, entry)
        else:
            items.append(entry)
        return RemoveEntry(self.section, str(self.entry["id"]))

    def describe(self, doc, words):
        return words("plan_edits.restore_entry", title=self.entry.get("title", self.entry.get("id")))


def _plain(x: Any) -> dict[str, Any] | None:
    if x is None:
        return None
    def conv(v: Any) -> Any:
        if isinstance(v, date):
            return v.isoformat()
        if isinstance(v, dict) or hasattr(v, "items"):
            return {str(k): conv(w) for k, w in dict(v).items()}
        if isinstance(v, (list, tuple)):
            return [conv(w) for w in v]
        return v

    return conv(x)


EDITS: dict[str, type] = {
    c.op: c for c in (SetGoalTarget, SetDeadlineDue, AddTarget, RemoveTarget, RestoreTarget, RemoveEntry, RestoreEntry)
}


def to_dict(edit: PlanEdit) -> dict[str, Any]:
    return {"op": edit.op, **asdict(edit)}


def from_dict(d: dict[str, Any]) -> PlanEdit:
    d = dict(d)
    op = d.pop("op", None)
    cls = EDITS.get(op)
    if cls is None:
        raise Refused(f"unknown edit {op!r}", lambda words: words("plan_edits.unknown_edit"))
    fields = {f for f in cls.__dataclass_fields__ if f != "op"}
    return cls(**{k: v for k, v in d.items() if k in fields})
