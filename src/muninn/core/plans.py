"""The plans as an actor: changing a plan, creating a domain from a drafted plan, and putting
one away. Every change goes through the gate, so whether it is done at once (the user's own
words) or asked first (the model's proposal) is the rule table's to decide, and each can be
undone. PlanBase stays the read side everyone reads.

A drafted plan's goals and deadlines are unconfirmed until the user says yes in the plan
review; each says what it is based on (its `source`). A domain is sensitive unless the draft
says otherwise. What a domain grew out of (e.g. "idea:3") is passed along: `on_created` and
`on_uncreated` tell its owner, so an idea can be marked promoted and put back on undo.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Callable, Sequence

from . import plan_edits
from .actions import ActionGate, DecidedBy, DomainArchived, DomainCreated, PlanChange, Ran
from .models import DomainPlan
from .language import Language


class Plans:
    def __init__(self, gate: ActionGate, plan: Callable[[str], DomainPlan | None],
                 on_created: Callable[[str, str], Any] = lambda domain, source: None,
                 on_uncreated: Callable[[str, str], Any] = lambda domain, source: None,
                 week_hours: Callable[[], Sequence[tuple[str, float]]] = lambda: (),
                 today: Callable[[], date] = date.today, tz: str = "UTC", *, words: Language):
        """`plan`: a domain's plan, unconfirmed items included; `on_created(domain, source)` and
        `on_uncreated(domain, source)` tell whoever the source belongs to; `week_hours`: the weekly
        hours goals already in the plans (title, hours), which a new domain's hours compete with."""
        self.words = words
        self._gate = gate
        self._plan = plan
        self._on_created = on_created
        self._on_uncreated = on_uncreated
        self._week_hours = week_hours
        self._today = today
        self._tz = tz

    def edit(self, domain: str, edit: Any, by: DecidedBy = DecidedBy.USER) -> Ran | None:
        """A change to a plan (plan_edits)."""
        return self._gate.run(PlanChange(domain, plan_edits.to_dict(edit)), by)

    def create(self, doc: dict[str, Any], by: DecidedBy, source: str = "") -> Ran | None:
        """A domain from a drafted plan: done at once when the user asked for it, a question
        first when it is the model's proposal. Sensitive unless the draft says otherwise."""
        dom = dict(doc.get("domain") or {})
        if not dom.get("id") or not dom.get("title"):
            raise ValueError(self.words("plans.needs_name"))
        dom.setdefault("sensitivity_default", "sensitive")
        return self._gate.run(DomainCreated({**doc, "domain": dom}, source), by)

    def archive(self, domain: str, by: DecidedBy = DecidedBy.USER) -> Ran | None:
        plan = self._plan(domain)
        if plan is None:
            raise ValueError(self.words("plans.no_domain", domain=domain))
        return self._gate.run(DomainArchived(domain, plan.title), by)

    def created(self, domain: str, source: str) -> None:
        if source:
            self._on_created(domain, source)

    def uncreated(self, domain: str, source: str) -> None:
        if source:
            self._on_uncreated(domain, source)

    def tools(self) -> list[Any]:
        """Changing a plan, and drafting a new domain."""
        from .tools.plan import ChangePlan, ProposeDomain

        return [ChangePlan(self._plan, lambda domain, edit: self.edit(domain, edit)),
                ProposeDomain(lambda doc, source: self.create(doc, DecidedBy.USER, source), self._week_hours, self._today, self._tz)]
