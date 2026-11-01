"""Reviewing a plan's suggestions with the user: every goal and deadline a plan
proposes waits for a yes, a change or a no before Muninn treats it as the
user's own. A decision is undoable."""

from __future__ import annotations

from typing import Any, Callable, Protocol, Sequence

from . import plan_edits
from .actions import ActionGate, DecidedBy, DomainReviewed, Ran
from .models import ActionRecord, DomainPlan
from .ports import ActionLog, Clock, CompletionStore, StateStore
from .timewin import local_date
from .language import Language


def review_id(domain: str, kind: str, ident: str) -> str:
    return f"{domain}/{kind}:{ident}"


def parse_review_id(rid: str) -> tuple[str, str, str]:
    domain, _, rest = rid.partition("/")
    kind, _, ident = rest.partition(":")
    if kind not in ("goal", "deadline") or not ident:
        raise ValueError(f"bad review id {rid}")
    return domain, kind, ident


class ReviewStorage(StateStore, CompletionStore, ActionLog, Protocol):
    """The decisions (state "plan_review"), deadlines already done, and the actions logged for undo."""


class PlanReview:
    kinds = ("review",)  # as a topic: a plan item waiting for the user's yes or no
    def __init__(self, store: ReviewStorage, clock: Clock, tz: str, edit_plan: Callable[[str, Any], Any],
                 all_plans: Callable[[], Sequence[DomainPlan]], plan: Callable[[str], DomainPlan | None],
                 talk: Callable[[str], Any], gate: ActionGate | None = None, *, words: Language):
        """`edit_plan(domain, edit)` writes a plan edit and returns its inverse; `plan`: a domain's plan,
        unconfirmed items included; `talk`: the latest discussion about a review item."""
        self.words = words
        self._store = store
        self._clock = clock
        self._tz = tz
        self._edit_plan = edit_plan
        self._all_plans = all_plans
        self._plan = plan
        self._talk = talk
        self._gate = gate  # taking a whole domain at once goes through it

    def decisions(self) -> dict[str, str]:
        return dict(self._store.get_state("plan_review") or {})

    def decide(self, rid: str, decision: str | None) -> None:
        d = self.decisions()
        if decision is None:
            d.pop(rid, None)
        else:
            d[rid] = decision
        self._store.set_state("plan_review", d)

    def queue(self) -> list[dict[str, Any]]:
        """Goals and deadlines still waiting for the user's yes or no,
        highest-priority domain and most urgent first."""
        decided = self.decisions()
        today = local_date(self._clock.now(), self._tz)
        out = []
        for p in sorted(self._all_plans(), key=lambda p: p.priority):
            for g in sorted(p.goals, key=lambda g: g.priority):
                rid = review_id(p.id, "goal", g.id)
                if rid not in decided:
                    amount = (self.words("review.amount", period=g.period, target=g.target, metric=g.metric)
                              if g.metric not in ("late_activity",) else self.words("review.no_activity_after", cutoff=g.params.get("cutoff", "")))
                    out.append({"id": rid, "kind": "goal", "domain": p.id, "domain_title": p.title, "title": g.title,
                                "detail": amount, "value": g.target, "actions": list(g.if_behind[:2]),
                                "source": g.params.get("source")})
            completed = self._store.completed()
            for d in sorted(p.deadlines, key=lambda d: d.due):
                rid = review_id(p.id, "deadline", d.id)
                if rid not in decided and f"{p.id}/{d.id}" not in completed:
                    out.append({"id": rid, "kind": "deadline", "domain": p.id, "domain_title": p.title, "title": d.title,
                                "detail": self.words("review.deadline_detail", due=d.due, days=(d.due - today).days), "value": d.due.isoformat(),
                                "actions": [], "source": d.source})
        for it in out:
            it["talk"] = self._talk(it["id"])
        return out

    def decide_item(self, rid: str, decision: str, value: Any = None) -> ActionRecord:
        """decision: confirm | change (with value) | reject. Returns the
        (undoable) record of what happened."""
        domain, kind, ident = parse_review_id(rid)
        plan = self._plan(domain)
        if plan is None:
            raise ValueError(self.words("review.no_such", what=domain))
        item = plan.goal(ident) if kind == "goal" else next((d for d in plan.deadlines if d.id == ident), None)
        if item is None:
            raise ValueError(self.words("review.no_such", what=ident))
        now = self._clock.now()
        if decision == "confirm":
            self.decide(rid, "confirmed")
            text = self.words("review.confirmed", title=item.title)
            undo = {"kind": "review", "id": rid}
        elif decision == "change":
            edit = plan_edits.SetGoalTarget(ident, float(value)) if kind == "goal" else plan_edits.SetDeadlineDue(ident, str(value))
            inverse = self._edit_plan(domain, edit)
            self.decide(rid, "confirmed")
            text = self.words("review.changed", title=item.title, value=value)
            undo = {"kind": "review_edit", "id": rid, "domain": domain, "edit": plan_edits.to_dict(inverse)}
        elif decision == "reject":
            inverse = self._edit_plan(domain, plan_edits.RemoveEntry("goals" if kind == "goal" else "deadlines", ident))
            self.decide(rid, "rejected")
            text = self.words("review.removed", title=item.title)
            undo = {"kind": "review_edit", "id": rid, "domain": domain, "edit": plan_edits.to_dict(inverse)}
        else:
            raise ValueError(f"unknown decision {decision}")
        return self._store.log_action(ActionRecord(None, now, domain, text, undo))

    def topic(self, kind: str, ref: str) -> dict[str, Any] | None:
        it = next((x for x in self.queue() if x["id"] == ref), None)
        if it is None:
            return None
        return {"kind": "review", "ref": ref, "domain": it["domain"], "item": it["kind"], "title": it["title"],
                "detail": it["detail"], "value": it["value"], "source": it.get("source"),
                "if_behind": it.get("actions") or []}

    def tools(self) -> list[Any]:
        """Deciding on the item being discussed."""
        from .tools.plan import DecideItem

        return [DecideItem(self.decide_item)]

    def confirm_domain(self, domain: str, by: DecidedBy = DecidedBy.USER) -> Ran | None:
        """Every item of a domain still waiting for the user, confirmed at once (a drafted domain
        taken whole); undo puts them all back to waiting. None when nothing waits."""
        ids = tuple(it["id"] for it in self.queue() if it["domain"] == domain)
        if not ids:
            return None
        plan = self._plan(domain)
        return self._gate.run(DomainReviewed(domain, plan.title if plan else domain, ids), by)
