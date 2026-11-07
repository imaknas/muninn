"""The engine: observing (collecting, attributing what was observed), evaluating the plans and
notifying, the scheduled reports, and running the actors' periodic jobs. Every entry point is
idempotent: running it twice does not duplicate inbox items or pushes. It imports no actor; the
front object (core/assembly.py) wires the actors to it."""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Mapping, Sequence

from . import plan_edits
from .actors import Job
from .attribution import Attributor
from .cases import CaseFeedback
from .composer import Composer, Composition, DomainPolicy
from .drift import DriftRule, EvalContext, evaluate_all
from .handoffs import Handoffs
from .llm_router import LLMRequest, LLMRouter
from .metrics import PASSIVE_ACTIVITY_KINDS, Metric, estimate_hours, span_of
from .models import ActionRecord, Confirmation, DomainPlan, InboxItem, Observation, Sensitivity, Signal
from .open_items import registered_marker
from .plan_base import AttributionConfig, PlanBase
from .planner import build_agenda, plan_domain
from .ports import Calendar, Channel, Classifier, Clock, Connection, NoProjectRecords, PlanRepo, ProjectRecords, Source, Store
from .privacy import DayFacts, evening_payload
from .progress import INCLUSIVE, deadline_statuses, goal_progress, measure_window
from .reports import evening_summary, morning_brief, weekly_report
from .timewin import ActiveHours, local, local_date, period_window
from .todo_match import TodoMatcher
from .understanding import Understanding
from .language import Language, written_in

log = logging.getLogger(__name__)

MUNINN_SCHEDULES = "Muninn 有排"  # how the projects' to-do lists mark an item Muninn itself keeps

NONE_OF_THESE = "都不是"  # the answer the app sends for "none of these domains" (not shown: the card has its own label)


EVENING_SYSTEM = (
    "You are a concise, direct personal assistant. From the JSON the user gives (today's progress numbers and to-do titles), "
    "write a 2 to 4 sentence evening review in {language}: what got done today, where things are behind, and the first thing for tomorrow. "
    "Use only the information in the JSON, and say it directly."
)


def evening_system(words: Language) -> str:
    return written_in(EVENING_SYSTEM, words)


class Engine:
    """Observing, attributing, notifying and scheduling. It knows no actor: what it needs from
    them is passed in (the plan review's decisions, the named places, the review queue, the day's
    schedule, where the user was), and the periodic work it runs are the jobs the actors publish."""

    AUTO_TAG = "muninn:auto"

    def __init__(
        self,
        *,
        store: Store,
        plans: PlanRepo,
        sources: Sequence[Source],
        channel: Channel,
        router: LLMRouter,
        classifier: Classifier | None,
        clock: Clock,
        metrics: Mapping[str, Metric],
        rules: Sequence[DriftRule],
        composer: Composer,
        attribution: AttributionConfig,
        hours: ActiveHours = ActiveHours(),
        tz: str = "UTC",
        records: ProjectRecords | None = None,
        calendar: Calendar | None = None,
        connections: Sequence[Connection] = (),
        understanding: Understanding | None = None,
        cases: CaseFeedback | None = None,
        handoffs: Handoffs | None = None,
        todo_matcher: TodoMatcher | None = None,
        owner_label: str = "owner",
        decisions: Callable[[], Mapping[str, str]] = lambda: {},
        places: Callable[[], Sequence[Any]] = lambda: [],
        review_queue: Callable[[], list[dict[str, Any]]] = lambda: [],
        schedule_today: Callable[..., list[Any]] = lambda **kw: [],
        places_line: Callable[..., str | None] = lambda day=None: None,
        jobs: Callable[[], Sequence[Job]] = lambda: (), words: Language,
    ):
        """`decisions`: the plan review's; `places`: the named places; `review_queue`: plan items
        waiting for the user (the morning brief counts them); `schedule_today`: puts the day's
        blocks on the calendar; `places_line`: where the user was today (the evening summary);
        `jobs`: the actors' periodic work."""
        self.words = words
        self.store = store
        # the plans and the domains: the base every actor stands on
        self.planbase = PlanBase(plans, attribution, store, metrics, hours, decisions, places)
        self.understanding = understanding or Understanding(router)
        self.sources = list(sources)
        self.channel = channel
        self.still_open: Callable[[], list[str]] = lambda: []  # what waits on the user, a sentence each (the front sets it)
        self.router = router
        self.classifier = classifier
        self.clock = clock
        self.metrics = metrics
        self.rules = list(rules)
        self.composer = composer
        self.hours = hours
        self.tz = tz
        self.records: ProjectRecords = records or NoProjectRecords()
        self.calendar = calendar
        self.connections = list(connections)
        self.cases = cases or CaseFeedback(store, clock)
        self.handoff_log = handoffs or Handoffs(store, clock, self.cases)
        self.registered = registered_marker(owner_label)  # a project line that points at a registered task
        self.todo_matcher = todo_matcher or TodoMatcher(router, store)
        self.review_queue = review_queue
        self.schedule_today = schedule_today
        self.places_line = places_line
        self.jobs = jobs

    @property
    def plans(self) -> PlanRepo:
        return self.planbase.repo

    @plans.setter
    def plans(self, repo: PlanRepo) -> None:
        self.planbase.repo = repo

    @property
    def attr_cfg(self) -> AttributionConfig:
        return self.planbase.attribution

    @attr_cfg.setter
    def attr_cfg(self, cfg: AttributionConfig) -> None:
        self.planbase.attribution = cfg

    def _all_plans(self) -> list[DomainPlan]:
        return self.planbase.all_plans()

    def _plans(self) -> list[DomainPlan]:
        return self.planbase.plans()

    def _plan(self, domain: str, *, include_unconfirmed: bool = False) -> DomainPlan | None:
        return self.planbase.plan(domain, include_unconfirmed=include_unconfirmed)

    def hour_goals(self) -> list[tuple[str, str]]:
        return self.planbase.hour_goals()

    def calendar_view(self, start: datetime, end: datetime, cloud: bool = False) -> list[dict[str, Any]]:
        """The user's own events (every calendar but Muninn's, declined ones
        left out) for the model; empty when the calendar can't be read."""
        if self.calendar is None:
            return []
        try:
            if not self.calendar.available():
                return []
            events = self.calendar.events(start, end)
        except Exception as e:  # an unreachable calendar must not break a conversation
            log.warning("calendar events unavailable: %s", e)
            return []
        return [{"title": ev.title, "start": f"{local(ev.start, self.tz):%Y-%m-%d %H:%M}", "end": f"{local(ev.end, self.tz):%H:%M}",
                 "calendar": ev.calendar} for ev in events
                if not (cloud and self.understanding.is_work_line(ev.title))][:80]

    def understanding_plans(self, cloud: bool) -> list[DomainPlan]:
        return self.planbase.understanding_plans(cloud)

    def work_projects(self) -> set[str]:
        return self.planbase.work_projects()

    def projects_by_domain(self) -> dict[str, list[str]]:
        return self.planbase.projects_by_domain()

    def _goal_progress(self, plan: DomainPlan, goal: Any, now: datetime) -> Any:
        return self.planbase.goal_progress(plan, goal, now)

    def domains(self, plans: Sequence[DomainPlan] | None = None) -> list[tuple[str, str]]:
        return self.planbase.domains(plans)

    def attributor(self, plans: Sequence[DomainPlan]) -> Attributor:
        return self.planbase.attributor(plans)

    def _ctx(self, plans: Sequence[DomainPlan], now: datetime) -> EvalContext:
        return EvalContext(now, plans, self.store, self.metrics, self.store.completed(), self.hours, words=self.words)

    def _deliver(self, comp: Composition, now: datetime) -> None:
        if not comp.pushes:
            return
        delivered = self.channel.send(comp.pushes)
        if delivered:
            for m in comp.pushes:
                if m.domain in comp.tiers:  # alerts count toward quotas; reports do not
                    self.store.record_push(local_date(now, self.tz), now, m.domain, comp.tiers[m.domain])

    def ingest(self, observations: Sequence[Observation], *, evaluate: bool = True) -> list[Observation]:
        plans = self._plans()
        attr = self.attributor(plans)
        new = self.store.add_observations(attr.attribute(o) for o in observations)
        if new and evaluate:
            self.evaluate(plans)
        return new

    def collect(self) -> list[Observation]:
        now = self.clock.now()
        plans = self._plans()
        found: list[Observation] = self._tracker_changes(plans, now)
        for src in self.sources:
            cursor_key = f"cursor:{src.name}"
            try:
                obs, cursor = src.collect(self.store.get_state(cursor_key), now)
            except Exception as e:
                log.warning("source %s failed: %s", src.name, e)
                continue
            found += obs
            self.store.set_state(cursor_key, cursor)
        attr = self.attributor(plans)
        return self.store.add_observations(attr.attribute(o) for o in found)

    def _tracker_changes(self, plans: Sequence[DomainPlan], now: datetime) -> list[Observation]:
        """A changed plan/tracker counts as activity in that domain."""
        out = []
        for p in plans:
            fp = hashlib.sha1(repr((p.goals, p.deadlines, p.targets, p.tracker)).encode()).hexdigest()[:16]
            key = f"fingerprint:{p.id}"
            prev = self.store.get_state(key)
            if prev != fp:
                self.store.set_state(key, fp)
                if prev is not None:
                    out.append(Observation("plans", now, "tracker_change", f"plans:{p.id}:{fp}", {}, p.id, None, p.sensitivity_default))
        return out

    def suggest_attributions(self, lookback: timedelta = timedelta(days=7), limit: int = 5) -> int:
        """Asks the user (with a local-model suggestion when available) where
        unattributed repos belong. Nothing is attributed until they answer."""
        now = self.clock.now()
        asked = set(self.store.get_state("attribution:asked") or [])
        repos: dict[str, set[str]] = {}
        for o in self.store.unattributed(500):
            repo = o.payload.get("repo")
            if repo and repo not in asked and o.ts >= now - lookback:
                repos.setdefault(repo, set()).update(o.payload.get("langs") or ())
        domains = self.domains()
        created = 0
        for repo, langs in sorted(repos.items())[:limit]:
            suggestion = None
            if self.classifier:
                suggestion = self.classifier.classify_domain({"repo": repo, "languages": sorted(langs)}, domains)
            options = self._attribution_options(suggestion, domains)
            titles = dict(domains)
            q = self.words("engine.which_domain", repo=repo)
            if suggestion:
                q += self.words("engine.suggested", domain=titles.get(suggestion, suggestion))
            c = self.store.add_confirmation(
                Confirmation(None, now, "attribution", q, tuple(options), {"rule": "repo", "match": repo, "suggestion": suggestion}, key=f"attr:repo:{repo}")
            )
            asked.add(repo)
            created += c is not None
        self.store.update_state("attribution:asked", lambda now: sorted(set(now or ()) | asked))
        return created

    @staticmethod
    def _attribution_options(suggestion: str | None, domains: Sequence[tuple[str, str]]) -> list[str]:
        ids = [d for d, _ in domains]
        first = [suggestion] if suggestion in ids else []
        return first + [d for d in ids if d != suggestion] + [NONE_OF_THESE]

    def reattribute_unattributed(self, limit: int = 20_000) -> int:
        """Applies the current rules to stored observations that no rule matched
        when they arrived (rules are added later, in attribution.yaml or by
        answering a confirmation). Returns how many got a domain."""
        attr = self.attributor(self._plans())
        n = 0
        for o in self.store.unattributed(limit):
            a = attr.attribute(o)
            if a.domain is not None or a.goal != o.goal:
                self.store.reattribute(o.key, a.domain, a.goal, a.sensitivity)
                n += a.domain is not None
        return n

    def pending_confirmations(self) -> list[Confirmation]:
        """Open questions, with attribution options taken from the domains that
        exist now (plans may have been added since the question was asked).
        Attribution questions a rule has since answered are left out."""
        domains = self.domains()
        unresolved = self.store.unattributed_repos(20_000)
        ruled = {str(r) for r in (self.attr_cfg.rules.get("repos") or {})} | {
            k.split(":", 1)[1] for k in self.store.learned_rules() if k.startswith("repo:")}
        out = []
        for c in self.store.confirmations():
            if c.kind == "attribution":
                if c.data.get("match") not in unresolved or (c.data.get("rule") == "repo" and c.data.get("match") in ruled):
                    continue  # answered since: by what is left unplaced, or by a rule for that repo
                c = Confirmation(
                    c.id, c.ts, c.kind, c.question,
                    tuple(self._attribution_options(c.data.get("suggestion"), domains)),
                    {**c.data, "titles": dict(domains), "observations": unresolved[c.data["match"]]}, c.answer, c.key,
                )
            out.append(c)
        return out

    def attribution_decisions(self) -> list[dict[str, Any]]:
        """Repos the user has already placed (or declined to place), newest
        first, with where they are now."""
        learned = self.store.learned_rules()
        seen: set[str] = set()
        out = []
        for c in self.store.confirmations(pending_only=False):
            repo = c.data.get("match")
            if c.kind != "attribution" or c.answer is None or repo in seen:
                continue
            seen.add(repo)
            rule = learned.get(f"repo:{repo}")
            out.append({"repo": repo, "domain": rule["domain"] if rule else None})
        return out

    def forget_repo(self, repo: str) -> int:
        """For a 'repo' that is not a project (e.g. a home directory)."""
        n = self.store.forget_repo(repo)
        self.reattribute_unattributed()
        return n

    def set_repo_domain(self, repo: str, domain: str | None) -> int:
        """Changes (or with None, removes) the user's rule for a repo and
        re-attributes everything already observed from it. Returns how many
        observations were re-attributed. Sensitivity is still never lowered."""
        if domain is not None and domain not in {d for d, _ in self.domains()}:
            raise ValueError(f"unknown domain {domain}")
        if domain is None:
            self.store.forget_rule("repo", repo)
        else:
            self.store.learn_rule("repo", repo, domain, None)
        return self.reattribute_repo(repo)

    def reattribute_repo(self, repo: str) -> int:
        """Everything observed from a repo, attributed again by the rules as they are now."""
        attr = self.attributor(self._plans())
        n = 0
        for o in self.store.observations_for_repo(repo):
            a = attr.attribute(o.with_attribution(None, None, o.sensitivity))
            self.store.reattribute(o.key, a.domain, a.goal, a.sensitivity)
            n += 1
        return n

    @staticmethod
    def policies(plans: Sequence[DomainPlan]) -> dict[str, DomainPolicy]:
        return {p.id: DomainPolicy(priority=p.priority, observe_only=p.observe_only) for p in plans}

    def evaluate(self, plans: Sequence[DomainPlan] | None = None) -> Composition:
        now = self.clock.now()
        plans = self._plans() if plans is None else plans
        signals = evaluate_all(self.rules, self._ctx(plans, now))
        comp = self.composer.compose_alerts(signals, now, self.policies(plans))
        self._deliver(comp, now)
        return comp

    def needs_relink(self) -> list[str]:
        out = []
        for c in self.connections:
            try:
                if c.status() == "relink":
                    out.append(c.name)
            except Exception as e:
                log.warning("connection %s status failed: %s", c.name, e)
        return out

    def remind_relink(self) -> list[InboxItem]:
        """One reminder a day per expired connection; acting stops until relinked."""
        now = self.clock.now()
        items = []
        for name in self.needs_relink():
            marker = f"relink:{name}:{local_date(now, self.tz).isoformat()}"
            item = InboxItem(None, now, "muninn", "info", self.words("engine.relink_title", name=name.capitalize()),
                             self.words("engine.relink_body"), {"relink": name})
            items += self._once(marker, now, lambda item=item: item)
        return items

    def _once(self, marker: str, now: datetime, build) -> list[InboxItem]:
        """Runs a scheduled report at most once per marker; a failure releases
        the marker so the next scheduler attempt can retry."""
        if not self.store.claim(marker, now):
            return []
        try:
            item = build()
            comp = self.composer.compose_report(item, now)
            self._deliver(comp, now)
            return list(comp.items)
        except Exception:
            self.store.release(marker)
            raise

    def morning(self) -> list[InboxItem]:
        now = self.clock.now()

        def build() -> InboxItem:
            self.collect()
            agenda = build_agenda(self._plans(), now, self.store, self.metrics, self.store.completed(), self.hours, words=self.words)
            mine = sum(1 for t in self.records.todos() if t.owner == "me")
            item = morning_brief(agenda.domains, agenda.day, now, project_todos=mine, words=self.words)
            if still := self.still_open():  # what waits on the user, carried into today; asking comes first
                data = {**item.data, "push_title": self.words("engine.morning_push", n=len(still), first=still[0])}
                item = InboxItem(item.id, item.ts, item.domain, item.kind, item.title,
                                 self.words("engine.waiting_on_you", items=self.words.join(still)) + "\n\n" + item.body, data)
            return item

        items = self._once(f"morning:{local_date(now, self.tz).isoformat()}", now, build)
        try:
            self.schedule_today()
        except Exception as e:
            log.warning("scheduling failed: %s", e)
        return items

    def day_facts(self, plan: DomainPlan, now: datetime) -> DayFacts:
        day = local_date(now, plan.timezone)
        day_start, day_end = period_window("day", now, plan.timezone)
        until = min(day_end, now + INCLUSIVE)
        goals = [p for g in plan.goals if (p := goal_progress(plan, g, now, self.store, self.metrics, self.hours))]
        today_by_goal = {
            g.id: v for g in plan.goals if (v := measure_window(plan, g, day_start, until, self.store, self.metrics)) is not None
        }
        alerts = [i for i in self.store.inbox(limit=500) if i.kind == "alert" and i.domain == plan.id and local_date(i.ts, plan.timezone) == day]
        activity = self.store.observations(day_start, until, domain=plan.id, kinds=PASSIVE_ACTIVITY_KINDS)
        return DayFacts(
            plan=plan,
            day=day,
            goals=goals,
            today_by_goal=today_by_goal,
            applied_today=[i.target for i in plan.tracker if i.applied == day],
            deadlines=[s for s in deadline_statuses(plan, now, self.store.completed()) if not s.done],
            alerts_today=len(alerts),
            activity_hours=estimate_hours([span_of(o) for o in activity], timedelta(minutes=20), timedelta(minutes=10)),
        )

    def evening_wording(self, facts: DayFacts) -> str | None:
        if facts.plan.observe_only:
            return None
        payload = evening_payload(facts)
        if payload is None:
            return None
        return self.router.complete(
            LLMRequest(system=evening_system(self.words), content=payload, sensitivity=facts.plan.sensitivity_default, domain=facts.plan.id,
                       task="evening")
        )

    def evening(self) -> list[InboxItem]:
        now = self.clock.now()
        day = local_date(now, self.tz)

        def build() -> InboxItem:
            self.collect()
            plans = self._plans()
            completed = self.store.completed()
            facts = [self.day_facts(p, now) for p in plans]
            wording = {f.plan.id: self.evening_wording(f) for f in facts}
            digest: dict[str, list[str]] = {}
            for i in self.store.inbox(limit=500):
                if i.kind == "alert" and i.data.get("digest_only") and local_date(i.ts, self.tz) == day:
                    digest.setdefault(i.domain, []).append(i.title)
            tomorrow_morning = datetime.combine(day + timedelta(days=1), time(9, 0), local(now, self.tz).tzinfo)
            tomorrow = [
                plan_domain(p, tomorrow_morning, self.store, self.metrics, completed, self.hours, words=self.words) for p in plans
            ]
            item = evening_summary(facts, tomorrow, wording, digest, day, now, places_line=self.places_line(), words=self.words)
            if still := self.still_open():  # said once in the evening: what still waits on the user
                item = InboxItem(item.id, item.ts, item.domain, item.kind, item.title,
                                 item.body + "\n\n" + self.words("engine.still_waiting", items=self.words.join(still)), item.data)
            return item

        return self._once(f"evening:{day.isoformat()}", now, build)

    def weekly(self) -> list[InboxItem]:
        now = self.clock.now()
        week_start, _ = period_window("week", now, self.tz)

        def build() -> InboxItem:
            completed = self.store.completed()
            agendas = [plan_domain(p, now, self.store, self.metrics, completed, self.hours, words=self.words) for p in self._plans()]
            return weekly_report(agendas, week_start.date(), now, words=self.words)

        return self._once(f"weekly:{week_start.date().isoformat()}", now, build)

    def _report(self, item: InboxItem, now: datetime) -> None:
        self._deliver(self.composer.compose_report(item, now), now)

    def delete_auto_event(self, event_id: str) -> None:
        if self.calendar is not None:
            self.calendar.delete(event_id)

    def _alert(self, signal: Signal, now: datetime) -> None:
        self._deliver(self.composer.compose_alerts([signal], now), now)

    def target_title(self, domain: str, target: str) -> str:
        plan = self._plan(domain, include_unconfirmed=True)
        return plan.target_title(target) if plan else target

    def describe_plan_edit(self, domain: str, edit: Any) -> str:
        return edit.describe(self._plan_doc(domain), self.words)

    def _plan_doc(self, domain: str) -> dict[str, Any]:
        """The plan as a plain mapping, for describing an edit."""
        plan = self._plan(domain, include_unconfirmed=True)
        if plan is None:
            raise ValueError(self.words("engine.no_domain", domain=domain))
        return {
            "goals": [{"id": g.id, "title": g.title, "target": g.target, "period": g.period} for g in plan.goals],
            "deadlines": [{"id": d.id, "title": d.title, "due": d.due.isoformat()} for d in plan.deadlines],
            "targets": [{"id": t.id, "title": t.title} for t in plan.targets],
        }

    def link_case(self, ref: str, case: int | None) -> None:
        """`ref` is "action:<id>", "confirm:<id>" or "handoff:<id>"."""
        self.cases.link(ref, case)

    def case_feedback(self, ref: str, feedback: dict[str, Any]) -> None:
        self.cases.record(ref, feedback)

    def handoffs(self, *, status: str | None = "pending", since: int = 0) -> list[dict[str, Any]]:
        return self.handoff_log.pending(status=status, since=since)

    def _add_handoff(self, **fields: Any) -> dict[str, Any]:
        return self.handoff_log.add(**fields)

    def set_handoff_status(self, hid: int, status: str) -> bool:
        return self.handoff_log.set_status(hid, status)

    def lease_projects(self, via: Sequence[str]) -> set[str]:
        """The projects some content came from: lent sessions ("lease:<id>"), or a project's answer ("project:<name>")."""
        out = set()
        for v in via:
            if v.startswith("project:"):
                out.add(v.removeprefix("project:"))
            lease = self.store.lease(v.removeprefix("lease:")) if v.startswith("lease:") else None
            if lease and lease.get("project"):
                out.add(str(lease["project"]))
        return out

    def confirmation_options(self, cid: int) -> list[str]:
        c = next((x for x in self.pending_confirmations() if x.id == cid), None)
        return list(c.options) if c else []

    def external_todos(self) -> dict[str, dict[str, list[str]]]:
        """owner → project → items, projects in domain-priority order."""
        prio = {p.id: p.priority for p in self._plans()}
        attr = self.attributor(self._plans())

        def rank(project: str) -> int:
            d = attr.attribute(Observation("records", self.clock.now(), "probe", "probe", {"repo": project})).domain
            return prio.get(d, 9) if d else 9

        # settled already: a to-do the user's news resolved (the project's own list
        # only catches up at its next nightly pass), and the projects' copies of
        # what Muninn itself schedules, which Muninn shows in its own places
        settled = {(h.get("project"), h.get("item")) for h in self.handoffs(status=None)
                   if h.get("effect") == "resolves" and h.get("status") in ("pending", "delivered", "done")}
        same = self.todo_matcher.known()  # the same task as one of Muninn's items, or listed earlier
        out: dict[str, dict[str, list[str]]] = {}
        indexed = list(enumerate(self.records.todos()))
        for n, t in sorted(indexed, key=lambda nt: (rank(nt[1].project), nt[1].project)):
            if (t.project, t.text) in settled or MUNINN_SCHEDULES in t.text or f"{t.project}:{n}" in same or self.registered(t.text):
                continue
            out.setdefault(t.owner, {}).setdefault(t.project, []).append(t.text)
        return out

    def add_note(self, text: str, domain: str | None) -> Observation | None:
        now = self.clock.now()
        key = "note:" + hashlib.sha1(f"{now.isoformat()}|{text}".encode()).hexdigest()[:16]
        new = self.ingest([Observation("pwa", now, "note", key, {"text": text}, domain, None, Sensitivity.PERSONAL)])
        return new[0] if new else None

    def _plan_change_answered(self, c: Confirmation, answer: str) -> None:
        """Yes to a plan change asked before the action gate (old cards still answer)."""
        if answer != c.options[0]:
            return
        try:
            edit = plan_edits.from_dict(c.data["edit"])
            inverse = self.plans.edit(c.data["domain"], edit)
        except plan_edits.Refused as e:
            raise ValueError(e.say(self.words)) from None
        self.store.log_action(ActionRecord(None, self.clock.now(), c.data["domain"], c.question.rstrip("?"),
                                           {"kind": "plan_edit", "domain": c.data["domain"], "edit": plan_edits.to_dict(inverse)}))
        self.collect()

    def _attribution_answered(self, c: Confirmation, answer: str) -> None:
        """Which domain an unknown repo belongs to: learned as a rule, and what waited on it attributed."""
        if answer != NONE_OF_THESE:
            self.store.learn_rule(c.data["rule"], c.data["match"], answer, None)
            self.reattribute_unattributed()

    def tick(self) -> Composition:
        """One pass: collect, the actors' early jobs, the attribution sweep, their late jobs, evaluate
        and notify, then the jobs that act on what was found. A failing job is logged and skipped."""
        self.collect()
        self.remind_relink()
        self._run_jobs("early")
        self.reattribute_unattributed()
        try:
            self.suggest_attributions()
        except Exception as e:
            log.warning("attribution suggestions failed: %s", e)
        self._run_jobs("late")
        comp = self.evaluate()
        self._run_jobs("after")
        return comp

    def _run_jobs(self, phase: str) -> None:
        for job in self.jobs():
            if job.phase != phase:
                continue
            try:
                job.run()
            except Exception as e:
                log.warning("%s failed: %s", job.name, e)
