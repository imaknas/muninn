"""The plans and what they say about the user's domains: the base the actors stand on.

The plans as written (suggested goals and deadlines included), the ones
Muninn acts on (only what the user confirmed in the plan review), the
domains, which of them are work, the attribution rules built from all that,
which projects belong to which domain, and a goal's progress. It depends on
no actor; what it needs from one (the review's decisions, the named places)
is passed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Mapping, Protocol, Sequence

from .answer import projects_by_domain
from .attribution import Attributor, rules_from_config
from .metrics import Metric
from .models import DomainPlan, Goal, Observation, Sensitivity
from .ports import ObservationStore, PlanRepo, RuleStore, StateStore
from .progress import GoalProgress, goal_progress
from .review import review_id
from .timewin import ActiveHours


@dataclass(frozen=True)
class AttributionConfig:
    rules: Mapping[str, Any] = field(default_factory=dict)  # attribution.yaml content
    sensitive_domains: frozenset[str] = frozenset()
    extra_domains: Mapping[str, str] = field(default_factory=dict)  # id → title, for domains without a plan


class BaseStorage(RuleStore, StateStore, ObservationStore, Protocol):
    """The attribution the user confirmed, the window labels, and the observations progress is measured on."""


class PlanBase:
    def __init__(self, repo: PlanRepo, attribution: AttributionConfig, store: BaseStorage, metrics: Mapping[str, Metric],
                 hours: ActiveHours, decisions: Callable[[], Mapping[str, str]], places: Callable[[], Sequence[Any]]):
        """`decisions`: the plan review's decisions (which suggestions the user confirmed);
        `places`: the named places (a stay there is attributed to the place's domain).
        `repo` and `attribution` may be replaced while running."""
        self.repo = repo
        self.attribution = attribution
        self._store = store
        self._metrics = metrics
        self._hours = hours
        self._decisions = decisions
        self._places = places

    def all_plans(self) -> list[DomainPlan]:
        """Plans as written, including goals and deadlines the user has not
        confirmed yet (for review, answers and edits)."""
        return self.repo.load()

    def plans(self) -> list[DomainPlan]:
        """What Muninn acts on: only goals and deadlines the user confirmed.
        A plan's suggestions stay quiet until the user says they want them."""
        decided = self._decisions()
        return [
            replace(p,
                    goals=tuple(g for g in p.goals if decided.get(review_id(p.id, "goal", g.id)) == "confirmed"),
                    deadlines=tuple(d for d in p.deadlines if decided.get(review_id(p.id, "deadline", d.id)) == "confirmed"))
            for p in self.all_plans()
        ]

    def plan(self, domain: str, *, include_unconfirmed: bool = False) -> DomainPlan | None:
        plans = self.all_plans() if include_unconfirmed else self.plans()
        return next((p for p in plans if p.id == domain), None)

    def hour_goals(self) -> list[tuple[str, str]]:
        """(title, "<domain>/<goal>") of every goal that counts hours, highest priority first."""
        return [(g.title, f"{p.id}/{g.id}") for p in sorted(self.all_plans(), key=lambda p: p.priority)
                for g in p.goals if g.metric == "hours"]

    def understanding_plans(self, cloud: bool) -> list[DomainPlan]:
        """Every plan (unconfirmed suggestions too); for the cloud, without the work domains."""
        plans = self.all_plans()
        if not cloud:
            return plans
        return [p for p in plans if p.id not in self.attribution.sensitive_domains and p.sensitivity_default < Sensitivity.SENSITIVE]

    def work_projects(self) -> set[str]:
        """Projects whose activity the attribution rules put in a work domain."""
        out = set()
        for repo, v in (self.attribution.rules.get("repos") or {}).items():
            domain = v if isinstance(v, str) else (v or {}).get("domain")
            if domain in self.attribution.sensitive_domains:
                out.add(str(repo))
        return out

    def domains(self, plans: Sequence[DomainPlan] | None = None) -> list[tuple[str, str]]:
        plans = self.plans() if plans is None else plans
        out = [(p.id, p.title) for p in plans]
        known = {d for d, _ in out}
        cfg = self.attribution
        out += [(d, t) for d, t in cfg.extra_domains.items() if d not in known]
        out += [(d, d) for d in sorted(cfg.sensitive_domains) if d not in known | set(cfg.extra_domains)]
        return out

    def attributor(self, plans: Sequence[DomainPlan]) -> Attributor:
        sens = {p.id: p.sensitivity_default for p in plans}
        for d in self.attribution.extra_domains:
            sens.setdefault(d, Sensitivity.SENSITIVE)
        return Attributor(
            rules_from_config(self.attribution.rules, self._store.learned_rules(), self._places(),
                              self._store.get_state("window_classes") or {}, self._store.shown_files()),
            sens,
            self.attribution.sensitive_domains,
        )

    def projects_by_domain(self) -> dict[str, list[str]]:
        return projects_by_domain(self.attribution.rules.get("repos") or {}, self._store.learned_rules())

    def goal_progress(self, plan: DomainPlan, goal: Goal, now: datetime) -> GoalProgress | None:
        return goal_progress(plan, goal, now, self._store, self._metrics, self._hours)
