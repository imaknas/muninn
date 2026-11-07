"""The front object: Muninn as the app and the command line see it. It builds the engine (the
observing and scheduling core) and the actors, wires each to what it needs, and offers what
callers ask for; most of its methods hand the call to the engine or to one actor."""

from __future__ import annotations

import logging
from pathlib import Path
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Mapping, Sequence

from .attribution import Attributor
from .engine import Engine
from .review import PlanReview, parse_review_id, review_id
from .window_classify import WindowClassifier, WindowLabels
from .composer import Composer, Composition
from .drift import DriftRule, EvalContext
from .llm_router import LLMRouter
from .metrics import Metric
from .models import ActionRecord, Confirmation, DomainPlan, InboxItem, Observation, Prepared, Signal
from .prepare import PacketPreparer, Packets
from .apply import Applications, FormFiller, Sites
from . import plan_edits
from .actions import ActionGate, AskSettled, DecidedBy, PacketApply, PlanChange, Ran, reversal
from .alerts import Alerts
from .ideas import Ideas
from .answer import Answerer
from .dossier import Dossier
from .offline_time import OfflineTime
from .platform_alerts import PlatformAlerts
from .push_health import PushHealth
from .releases import ReleaseFeed, ReleaseNews
from .open_items import DeadlineItems, OpenItems, ProjectTodoItems, TaskItems, TrackerItems
from .task_evidence import CompletionDetector
from .tasks import TaskRegistry, TaskActor
from .todo_match import ModelTaskMatch, TodoMatcher
from .project_messages import ProjectMessages
from .understanding import Understanding
from .cases import CaseFeedback
from .actors import Job, TopicRouter
from .plans import Plans
from .records import Records
from .views import Views
from .plan_base import AttributionConfig
from .commands import CommandContext, CommandResult, CommandRunner
from .conversation import Conversation
from .decide import LLMDecider
from .speak_up import Ask, SpeakUp
from .idea_next import IdeaNext, IdeaReports
from .topic_memory import TopicMemory
from .topics import OPEN, Topics
from . import waiting
from . import handoff_records, ph
from .questions import Questions
from .tools import ToolRegistry
from .tools.blocks import BlockTools
from .tools.builtin import ORDER
from .tools.attribution import AttributionTools
from .tools.conversation import ConversationTools
from .tools.plan import PlanTools
from .tools.records import TrackerTools
from .facts import ImpactAnalyzer, ImpactApplier
from .handoffs import Handoffs
from .language import Language
from .mail import Mailroom, MailTriager
from .onboarding import Onboarding
from .ports import PushMessage, Calendar, Channel, Classifier, Clock, Connection, HandoffOutbox, LLMBackend, Mailbox, NoSandboxes, PlanRepo, PostedInbox, ProjectInbox, ProjectRecords, PulledOutbox, RunningVersion, Sandboxes, Source, Store, TranscriptSource, UnknownVersion
from .requests import Requests
from .places import Place, Stay, Whereabouts
from .schedule import DURATIONS, Task, place
from .reflect import Reflection, Reflector, WeekFacts
from .privacy import DayFacts
from .timewin import ActiveHours, local, local_date

log = logging.getLogger(__name__)




# what the engine holds and the front reads (and tests and the command line replace) through it
CORE = ("store", "planbase", "understanding", "sources", "channel", "router", "classifier", "clock", "metrics", "rules",
        "composer", "hours", "tz", "records", "calendar", "connections", "cases", "handoff_log", "registered", "todo_matcher")


class Muninn:
    """The engine and the actors, wired; what the app, the command line and the tests talk to."""

    AUTO_TAG = Engine.AUTO_TAG

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
        words: Language,
        hours: ActiveHours = ActiveHours(),
        tz: str = "UTC",
        preparer: PacketPreparer | None = None,
        prepare_per_tick: int = 1,
        transcripts: TranscriptSource | None = None,
        reflector: Reflector | None = None,
        records: ProjectRecords | None = None,
        calendar: Calendar | None = None,
        shown_calendar: Calendar | None = None,
        mailboxes: Sequence[Mailbox] = (),
        triager: MailTriager | None = None,
        connections: Sequence[Connection] = (),
        window_classifier: WindowClassifier | None = None,
        understanding: Understanding | None = None,
        cases: CaseFeedback | None = None,
        handoffs: Handoffs | None = None,
        messages: ProjectMessages | None = None,
        offline: OfflineTime | None = None,
        todo_matcher: TodoMatcher | None = None,
        tasks: TaskRegistry | None = None,
        open_items: OpenItems | None = None,
        owner_label: str = "owner",
        leases: Any = None,
        gate: ActionGate | None = None,
        whereabouts: Whereabouts | None = None,
        mailroom: Mailroom | None = None,
        packets: Packets | None = None,
        sites: Sites | None = None,
        form_filler: FormFiller | None = None,
        documents: Any = None,
        keep_picture: Callable[[str, bytes], str] | None = None,
        windows: WindowLabels | None = None,
        plan_review: PlanReview | None = None,
        reflection_desk: Reflection | None = None,
        project_inbox: ProjectInbox | None = None,
        handoff_outbox: HandoffOutbox | None = None,
        sandboxes: Sandboxes | None = None,
        parties: ph.Parties | None = None,
        ph_extensions: dict[str, ph.Ext] | None = None,
        running: RunningVersion | None = None,
        release_feed: ReleaseFeed | None = None,
        installed_version: str = "",
        install_kind: str = "other",
        push_health: PushHealth | None = None,
    ):
        self.words = words  # the language Muninn speaks to the user in
        self._shown_calendar = shown_calendar  # what the screens read; what Muninn decides from reads `calendar` itself
        # the core: observing, attributing, notifying, scheduling; what it needs from the actors comes through these
        self.engine = Engine(
            store=store, plans=plans, sources=sources, channel=channel, router=router, classifier=classifier, clock=clock,
            metrics=metrics, rules=rules, composer=composer, attribution=attribution, hours=hours, tz=tz, records=records,
            calendar=calendar, connections=connections, understanding=understanding, cases=cases, handoffs=handoffs,
            todo_matcher=todo_matcher, owner_label=owner_label,
            decisions=lambda: self.plan_review.decisions(), places=lambda: self.whereabouts.places(),
            review_queue=lambda: self.review_queue(), schedule_today=lambda **kw: self.schedule_today(**kw),
            places_line=lambda day=None: self.places_line(day), jobs=lambda: self.jobs(), words=words)
        self.records_actor = Records(store, clock, tz, lambda: self.plans,
                                     lambda domain: self._plan(domain, include_unconfirmed=True), lambda obs: self.ingest(obs),
                                     lambda: self.collect(), lambda: self.evaluate(), words=words)
        self.prepare_per_tick = prepare_per_tick
        # actions go through the gate (more kinds move over one at a time); it acts on this engine
        self.gate = gate or ActionGate(store, clock, lambda: self, words=words)
        # the project side beyond its records: messages from the projects, handoffs to them, exploration projects
        self.project_inbox = project_inbox or PostedInbox()
        self.handoff_outbox = handoff_outbox or PulledOutbox()
        self.sandboxes = sandboxes or NoSandboxes()
        # changing a plan, creating and putting away a domain
        self.plans_actor = Plans(self.gate, lambda domain: self._plan(domain, include_unconfirmed=True),
                                 on_created=lambda domain, source: self.ideas.promoted(domain, source),
                                 on_uncreated=lambda domain, source: self.ideas.unpromoted(domain, source),
                                 week_hours=lambda: [(f"{p.title}·{g.title}", float(g.target)) for p in self._plans() for g in p.goals
                                                     if g.metric == "hours" and g.period == "week"],
                                 today=lambda: local_date(self.clock.now(), self.tz), tz=tz, words=words)
        self.tasks = tasks or TaskRegistry(store, clock, self.handoff_log, ModelTaskMatch(router), self.gate, on_close=self.task_closed, words=words)
        self.messages = messages or ProjectMessages(store, clock, self.handoff_log, self.tasks,
                                                    projects=lambda: self.records.projects(), answered=self.project_answered,
                                                    words=words)
        self.leases = leases  # ChannelLease (lending a thread to a project's session), None when not set up
        self.permissions = None  # PermissionDesk (what a lent session asks to do), set with the leases
        # Principal Handoff: who the user and Muninn are in its records, and the extensions Muninn implements
        self.parties = parties or handoff_records.parties_from(None, owner_label)
        vendor = Path(__file__).resolve().parents[1] / "contracts" / "vendor"
        exts = ph_extensions or ph.extensions(vendor)
        self.handoff_versions = handoff_records.Versions(store)
        self.records_in = handoff_records.Inbox(store, clock, self.parties, exts, self.messages, self.tasks, self.handoff_versions, words=words)
        self.records_out = handoff_records.Outbox(store, clock, self.parties, exts, self.handoff_versions, words=words)
        self.open_items = open_items or OpenItems([
            DeadlineItems(self.understanding_plans, lambda: self.store.completed(), self.review_decisions),
            TrackerItems(self.understanding_plans),
            TaskItems(lambda: self.tasks.open(), self.work_projects),
            ProjectTodoItems(lambda: self.records.todos(), lambda text: self.registered(text), self.work_projects,
                             lambda text: self.understanding.is_work_line(text)),
        ])
        self.reflection_desk = reflection_desk or Reflection(
            store, clock, tz, transcripts, reflector, self.domains, lambda: self.attributor(self._plans()),
            lambda: self.attr_cfg.sensitive_domains, self.where_and_what, lambda: self.records, self._once, words=words)
        self.plan_review = plan_review or PlanReview(store, clock, tz, lambda d, e: self.plans.edit(d, e), self._all_plans,
                                                     lambda domain: self._plan(domain, include_unconfirmed=True),
                                                     lambda rid: self.conversation().suggestions_for("review", rid), gate=self.gate,
                                                     words=words)
        self.windows = windows or WindowLabels(store, clock, window_classifier, self.cases, self._all_plans, self.domains,
                                               lambda: self.attributor(self._plans()), self.reattribute_unattributed,
                                               tz=tz, clear=lambda text: self.understanding.clear(text), words=words)
        self.packets = packets or Packets(store, clock, tz, preparer, self._plans, self._plan, lambda name: self.router.is_local(name),
                                          self._report, self.set_status, self.gate, self.link_case, self.case_feedback,
                                          label=lambda name: self.router.label_of(name), words=words)
        # application forms filled from the packets (sites: where a form can be filled; documents: the résumé files,
        # .files() and .documents(); keep_picture: keeps a picture of a filled form, returning the id it is shown by)
        self.applications = Applications(store, clock, tz, sites or Sites(), form_filler, self.packets,
                                         documents.files if documents is not None else dict,
                                         documents.documents if documents is not None else list,
                                         keep_picture or (lambda name, data: ""), self.gate, self._report,
                                         lambda about, text, buttons: self.conversation().speak(about, text, buttons), words=words)
        self.packets.form_topic = self.applications.topic_extra
        self.mailroom = mailroom or Mailroom(store, clock, self.gate, mailboxes, triager, self._plans,
                                             lambda obs: self.ingest(obs, evaluate=False), self._alert,
                                             lambda title, context, due=None, links=(): self.tasks.add(title, due, links, context),
                                             words=words)
        self.whereabouts = whereabouts or Whereabouts(store, clock, tz, lambda: self.attr_cfg.rules.get("places") or [],
                                                      self.domains, self.reattribute_unattributed, self.gate, words=words)
        self.alerts = Alerts(store, self.gate, lambda: self.views().alerts(), words=words)
        self.engine.still_open = lambda: [w.says for w in self.waiting() if w.group == "wait"]
        self.ideas = Ideas(store, clock, self.gate, sandbox=lambda name, purpose: self.sandboxes.create(name, purpose),
                           upgrade=lambda name, rename: self.sandboxes.upgrade(name, rename),
                           rename_project=lambda name, to: self.sandboxes.rename(name, to),
                           renamed=self.project_renamed, words=words)
        # what the user asked of Muninn itself, and whether the Muninn running now includes it
        self.requests = Requests(store, clock, self.gate, running or UnknownVersion(words("assembly.no_version")),
                                 speak=lambda tid, text, via, buttons: self.conversation().speak(None, text, buttons, thread=tid, via=via),
                                 push=lambda title: None if self.composer.quiet(self.clock.now())
                                 else self.channel.send([PushMessage("muninn", 1, title=title)]), words=words)
        self.offline = offline or OfflineTime(store, clock, tz, hours, self.hour_goals,
                                              calendar.busy if calendar is not None else None, words=words)
        # a newer Muninn: said once per release (no feed: not checked)
        self.release_news = ReleaseNews(store, clock, tz, release_feed, installed_version, install_kind, self._report,
                                        store.add_inbox, words=words)
        # whether pushes reach a phone (the channel says when the push service took one; the phone, when it showed it)
        self.push_health = push_health or PushHealth(store, clock, tz, words=words, quiet=composer.quiet)
        # the platform's alerts: pushed outside the daily limit, held through quiet hours
        self.platform_alerts = PlatformAlerts(store, clock, self.composer.quiet,
                                              lambda title: self.channel.send([PushMessage("platform", 1, title=title)]))

    # ── helpers ─────────────────────────────────────────────────────────────

    # the plans and the domains (PlanBase in core/plan_base.py; these stay so callers need not change)

    @property
    def plans(self) -> PlanRepo:
        return self.engine.plans

    @plans.setter
    def plans(self, repo: PlanRepo) -> None:
        self.engine.plans = repo

    @property
    def attr_cfg(self) -> AttributionConfig:
        return self.engine.attr_cfg

    @attr_cfg.setter
    def attr_cfg(self, cfg: AttributionConfig) -> None:
        self.engine.attr_cfg = cfg

    # what the engine holds, read and replaced through here (tests and the command line swap some)
    def __getattr__(self, name: str) -> Any:
        if name in CORE:
            return getattr(self.__dict__["engine"], name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in CORE and "engine" in self.__dict__:
            setattr(self.engine, name, value)
        else:
            object.__setattr__(self, name, value)

    def _all_plans(self) -> list[DomainPlan]:
        return self.engine._all_plans()

    def _plans(self) -> list[DomainPlan]:
        return self.engine._plans()

    def _plan(self, domain: str, *, include_unconfirmed: bool = False) -> DomainPlan | None:
        return self.engine._plan(domain, include_unconfirmed=include_unconfirmed)

    # ── reviewing a plan's suggestions with the user ───────────────────────

    # plan review (PlanReview in core/review.py; these stay so callers need not change)

    def review_decisions(self) -> dict[str, str]:
        return self.plan_review.decisions()

    def decide(self, rid: str, decision: str | None) -> None:
        self.plan_review.decide(rid, decision)

    def waiting(self) -> list[waiting.Wait]:
        """What waits on the user, in the order it is shown: the one list every place reads."""
        topics = self.topics().today()
        leases, perms = self.leases, self.permissions
        return waiting.Waiting([
            waiting.relinks(self.needs_relink, words=self.words),
            waiting.permission_requests(perms.pending if perms else list, words=self.words),
            waiting.confirmations(self.pending_confirmations),
            waiting.topics(lambda: topics, words=self.words),
            waiting.project_questions(self.messages.open_questions, words=self.words),
            waiting.talk_questions(lambda: self.store.threads(200), words=self.words),
            waiting.plan_review(self.review_queue, words=self.words),
            waiting.ready_packets(lambda: self.store.prepared(status="ready"), words=self.words),
            waiting.due_tasks(self.tasks.open, lambda: local_date(self.clock.now(), self.tz), words=self.words),
            waiting.lent_talks(leases.active if leases else list, lambda: {t["thread"] for t in topics}, words=self.words),
        ], words=self.words).all()

    def review_queue(self) -> list[dict[str, Any]]:
        return self.plan_review.queue()

    def review(self, rid: str, decision: str, value: Any = None) -> ActionRecord:
        return self.plan_review.decide_item(rid, decision, value)

    def hour_goals(self) -> list[tuple[str, str]]:
        return self.engine.hour_goals()

    def focus(self, name: str, on: bool) -> None:
        self.offline.focus(name, on)

    def calendar_view(self, start: datetime, end: datetime, cloud: bool = False) -> list[dict[str, Any]]:
        return self.engine.calendar_view(start, end, cloud)

    def understanding_plans(self, cloud: bool) -> list[DomainPlan]:
        return self.engine.understanding_plans(cloud)

    def work_projects(self) -> set[str]:
        return self.engine.work_projects()

    def review_why(self, rid: str) -> str | None:
        domain, kind, ident = parse_review_id(rid)
        plan = self._plan(domain, include_unconfirmed=True)
        item = plan and (plan.goal(ident) if kind == "goal" else next((d for d in plan.deadlines if d.id == ident), None))
        if not item:
            return None
        # the question is asked of the answering model, which words the reply
        question = (f'Why is "{item.title}" in the plan? What was it written in from (which job postings, notes or records)? '
                    "If there is no evidence that the user themselves said to do this, say plainly that it was inferred while planning.")
        return self.answerer().answer(question, self.understanding.clear_domain(domain, item.title), task="why")

    def projects_by_domain(self) -> dict[str, list[str]]:
        return self.engine.projects_by_domain()

    def _goal_progress(self, plan: DomainPlan, goal: Any, now: datetime) -> Any:
        return self.engine._goal_progress(plan, goal, now)

    def impact_analyzer(self) -> ImpactAnalyzer:
        """What a fact the user told settles among everything still open."""
        def domains(cloud: bool) -> list[tuple[str, str]]:
            return [(d, t) for d, t in self.domains(self.understanding_plans(cloud))
                    if not (cloud and d in self.attr_cfg.sensitive_domains)]

        return ImpactAnalyzer(self.open_items, self.understanding, self.clock.now, domains, self.records.projects, self.work_projects,
                              words=self.words)

    def completion_detector(self) -> CompletionDetector:
        """Closes tasks the day's evidence shows done."""
        return CompletionDetector(self.store, self.clock, self.tasks, self.understanding.router,
                                  lambda since, until: self.calendar_view(since, until), self.link_case, words=self.words)

    def commands(self) -> CommandRunner:
        """Understands and carries out what the user types, with Muninn's tools as they are now."""
        context = CommandContext(self.clock, self.tz, self.understanding_plans, self.calendar,
                                 self.understanding.is_work_line, self.calendar_view, words=self.words)
        return CommandRunner(self.command_tools(), context, self.understanding,
                             sensitive_domains=lambda: self.attr_cfg.sensitive_domains,
                             add_note=lambda text: self.add_note(text, None), dossier=self.dossier, answerer=self.answerer,
                             impact_analyzer=self.impact_analyzer, apply_impact=self.apply_impact,
                             link_case=self.link_case, lease_projects=self.lease_projects, idea_next=self.idea_next(),
                             words=self.words)

    def promote_waiting(self, locked: Callable[[Callable[[], Any]], Any]) -> None:
        """Ideas waiting to be made projects until their session was done with its turn: made now, quietly; one
        that could not be is said where the idea is talked about."""
        for n, why in self.ideas.retry_waiting(locked):
            locked(lambda: self.conversation().speak({"kind": "idea", "id": str(n)}, self.words("assembly.not_made", why=why), []))

    def idea_reports(self) -> IdeaReports:
        """Experiments that came back, reported in their idea's conversation."""
        return IdeaReports(self.store, self.clock, self.idea_next(), self.ideas.of_sandbox, self.conversation().thread,
                           lambda tid, text, buttons, mark: self.conversation().speak(None, text, buttons, thread=tid, marks=[mark]),
                           self.composer.quiet, lambda title: self.channel.send([PushMessage("muninn", 1, title=title)]),
                           words=self.words)

    def idea_next(self) -> IdeaNext:
        """What Muninn does next with an idea, asked as the routing says."""
        return IdeaNext(LLMDecider(self.router, self.store.set_case_verdict), lambda n: self.ideas.topic("idea", str(n)), words=self.words)

    def command_tools(self) -> ToolRegistry:
        """The tools the actors publish for the command model, in the order the model reads them."""
        def full(domain: str) -> DomainPlan | None:
            return self._plan(domain, include_unconfirmed=True)

        return ToolRegistry.collect([
            TrackerTools(self._plan, self.gate),
            self.plans_actor,
            PlanTools(full, self._plans, self._goal_progress, self.gate, self.clock),
            BlockTools(calendar=self.calendar, clock=self.clock, tz=self.tz, hours=self.hours, gate=self.gate),
            self.mailroom,
            ConversationTools(lambda: self.home_view()["next"], self.add_feature_request, self.undoable_actions, self.undo_action,
                              can_lend=self.leases is not None),
            AttributionTools(lambda: self.domains(), self.whereabouts.saved_names, self.gate),
            self.plan_review,
            self.questions(),
            TaskActor(self.tasks, lambda project: self._project_domain()(project)),
            self.packets,
            self.applications,
            self.whereabouts,
            self.alerts,
            self.ideas,
            self.topics(),
            self.messages,
        ], ORDER)

    def answerer(self) -> Answerer:
        """Answers questions about the user's own plans and progress, from what Muninn holds now."""
        return Answerer(self.clock.now, self.understanding, self.understanding_plans, self.work_projects, self._goal_progress,
                        self.projects_by_domain, self.records.state, self.calendar_view, words=self.words)

    def dossier(self) -> Dossier:
        """Everything known about one domain, for a discussion of one of its items."""
        return Dossier(self.store, self.clock, lambda d: self._plan(d, include_unconfirmed=True), self.review_queue,
                       self._goal_progress, self.projects_by_domain, self.records.state, lambda: self.preparer, words=self.words)

    def domains(self, plans: Sequence[DomainPlan] | None = None) -> list[tuple[str, str]]:
        return self.engine.domains(plans)

    def attributor(self, plans: Sequence[DomainPlan]) -> Attributor:
        return self.engine.attributor(plans)

    def _ctx(self, plans: Sequence[DomainPlan], now: datetime) -> EvalContext:
        return self.engine._ctx(plans, now)

    def _deliver(self, comp: Composition, now: datetime) -> None:
        return self.engine._deliver(comp, now)

    # ── observing ───────────────────────────────────────────────────────────

    def ingest(self, observations: Sequence[Observation], *, evaluate: bool = True) -> list[Observation]:
        return self.engine.ingest(observations, evaluate=evaluate)

    def collect(self) -> list[Observation]:
        return self.engine.collect()

    def _tracker_changes(self, plans: Sequence[DomainPlan], now: datetime) -> list[Observation]:
        return self.engine._tracker_changes(plans, now)

    # window labels (WindowLabels in core/window_classify.py; these stay so callers need not change)

    @property
    def window_classifier(self) -> WindowClassifier | None:
        return self.windows.classifier

    @window_classifier.setter
    def window_classifier(self, classifier: WindowClassifier | None) -> None:
        self.windows.classifier = classifier

    def classify_windows(self, limit: int = 25) -> int:
        return self.windows.classify(limit)

    def attribute_by_context(self, limit: int = 5000) -> int:
        return self.windows.by_context(limit)

    def recheck_windows(self, limit: int = 15) -> int:
        return self.windows.recheck(limit)

    def relabel_window(self, key: str) -> None:
        self.windows.relabel(key)

    def set_window_domain(self, key: str, domain: str | None) -> None:
        self.windows.set_domain(key, domain)

    def suggest_attributions(self, lookback: timedelta = timedelta(days=7), limit: int = 5) -> int:
        return self.engine.suggest_attributions(lookback, limit)

    _attribution_options = staticmethod(Engine._attribution_options)

    def reattribute_unattributed(self, limit: int = 20_000) -> int:
        return self.engine.reattribute_unattributed(limit)

    def pending_confirmations(self) -> list[Confirmation]:
        return self.engine.pending_confirmations()

    def attribution_decisions(self) -> list[dict[str, Any]]:
        return self.engine.attribution_decisions()

    def forget_repo(self, repo: str) -> int:
        return self.engine.forget_repo(repo)

    def set_repo_domain(self, repo: str, domain: str | None) -> int:
        return self.engine.set_repo_domain(repo, domain)

    def reattribute_repo(self, repo: str) -> int:
        return self.engine.reattribute_repo(repo)

    # ── evaluating and notifying ────────────────────────────────────────────

    policies = staticmethod(Engine.policies)

    def evaluate(self, plans: Sequence[DomainPlan] | None = None) -> Composition:
        return self.engine.evaluate(plans)

    def needs_relink(self) -> list[str]:
        return self.engine.needs_relink()

    def remind_relink(self) -> list[InboxItem]:
        return self.engine.remind_relink()

    def tick(self) -> Composition:
        return self.engine.tick()

    def jobs(self) -> list[Job]:
        """The actors' periodic work, in the order it runs within each phase of a tick."""
        def tasks() -> None:
            self.tasks.sort_new()
            self.completion_detector().run()

        def project_messages() -> None:
            if raw := self.project_inbox.collect():
                self.messages.receive(raw)

        def deliver_handoffs() -> None:
            for hid in self.handoff_outbox.deliver(self.handoff_log.pending()):
                self.handoff_log.set_status(hid, "delivered")

        return [
            Job("reading the projects' messages", project_messages),
            Job("sorting or closing tasks", tasks),
            Job("matching to-dos", self.match_todos),
            *self.offline.jobs(),
            *self.release_news.jobs(),
            *self.platform_alerts.jobs(),
            *self.push_health.jobs(),
            *self.windows.jobs(),
            *self.mailroom.jobs(),
            *self.packets.jobs(self.prepare_per_tick),
            *self.applications.jobs(),
            Job("delivering handoffs", deliver_handoffs, phase="after"),
            Job("setting aside quiet topics", lambda: self.topics().shelve_quiet(), phase="after"),
            Job("keeping each topic's state", lambda: self.topic_memory().refresh(), phase="after"),
            Job("speaking up when the user pauses", lambda: self.speak_up().run(), phase="after"),
        ]

    def _once(self, marker: str, now: datetime, build) -> list[InboxItem]:
        return self.engine._once(marker, now, build)

    def morning(self) -> list[InboxItem]:
        return self.engine.morning()

    def day_facts(self, plan: DomainPlan, now: datetime) -> DayFacts:
        return self.engine.day_facts(plan, now)

    def evening_wording(self, facts: DayFacts) -> str | None:
        return self.engine.evening_wording(facts)

    def evening(self) -> list[InboxItem]:
        return self.engine.evening()

    def weekly(self) -> list[InboxItem]:
        return self.engine.weekly()

    # ── getting things ready ────────────────────────────────────────────────

    # packets (Packets in core/prepare.py; these stay so callers need not change)

    @property
    def preparer(self) -> PacketPreparer | None:
        return self.packets.preparer

    @preparer.setter
    def preparer(self, preparer: PacketPreparer | None) -> None:
        self.packets.preparer = preparer

    def packet_candidates(self, horizon_days: int = 7, *, include_prepared: bool = False) -> list[tuple[DomainPlan, str]]:
        return self.packets.candidates(horizon_days, include_prepared=include_prepared)

    def prepare_packets(self, limit: int = 1, only: str | None = None) -> list[Prepared]:
        return self.packets.prepare(limit, only)

    def upgrade_local_packets(self, limit: int = 1) -> list[Prepared]:
        return self.packets.upgrade_local(limit)

    def prepared_done(self, pid: int) -> Ran | None:
        return self.packets.done(pid)

    def dismiss_prepared(self, pid: int) -> Ran | None:
        return self.packets.dismiss(pid)

    def _report(self, item: InboxItem, now: datetime) -> None:
        return self.engine._report(item, now)

    def undo_action(self, aid: int) -> bool:
        # only an undo that happened counts against the judgment that made the action
        return self.gate.undo(aid, lambda text: self.case_feedback(f"action:{aid}", {"kind": "undone", "what": text}))

    def undoable_actions(self) -> list[ActionRecord]:
        """What Muninn did in the last two days that can still be taken back, newest first."""
        return [a for a in self.store.actions(self.clock.now() - timedelta(days=2)) if not a.undone and reversal(a.undo) is not None]

    @property
    def shown_calendar(self) -> Calendar | None:
        return self._shown_calendar or self.calendar

    @shown_calendar.setter
    def shown_calendar(self, calendar: Calendar | None) -> None:
        self._shown_calendar = calendar

    def views(self) -> Views:
        """What the app shows, read with what Muninn holds now."""
        return Views(self.store, self.clock, self.tz, self.hours, self.metrics, self.planbase, calendar=lambda: self.shown_calendar,
                     decisions=self.review_decisions, pending=self.pending_confirmations, todos=self.external_todos,
                     relink=self.needs_relink, day_stays=self.day_stays, open_tasks=lambda: self.tasks.open(), ideas=self.ideas.listed,
                     talk=lambda kind, ref: self.conversation().suggestions_for(kind, ref), label=self.router.label_of,
                     words=self.words)

    def home_view(self) -> dict[str, Any]:
        return self.views().home()

    def _alert_list(self) -> list[dict[str, Any]]:
        return self.views().alerts()

    def prepared_view(self, pid: int) -> dict[str, Any] | None:
        view = self.views().prepared(pid)
        if view is not None and (form := self.applications.view(pid)):
            view["form"] = form
        return view

    def send_application(self, pid: int) -> str | None:
        """The user pressed send on the packet's page, which shows the form as it will go: their yes to the question
        that asks it (asked now when none is open). What came of it, or None when there is no filled form to send."""
        p = self.packets.get(pid)
        st = self.applications.state(pid) or {}
        if p is not None and st.get("state") == "unsure":  # pressed once already: it may have gone
            return self.words("apply.unsure", title=p.title)
        if p is None or p.status != "ready" or st.get("state") not in ("filled", "held"):
            return None

        def asking() -> Any:
            return next((c for c in self.store.confirmations() if c.kind == ActionGate.ASK
                         and (c.data.get("action") or {}).get("name") == PacketApply.name and c.data["action"].get("id") == pid), None)

        if (c := asking()) is None:
            self.gate.run(PacketApply(pid, p.title), DecidedBy.USER)
            c = asking()
        if c is None or self.questions().answer(c.id, c.options[0]) is None:
            return None
        st = self.applications.state(pid) or {}
        if st.get("state") == "unsure":
            return self.words("apply.unsure", title=p.title)
        return self.words("actions.packet_applied", title=p.title, said=st.get("said", "")) if st.get("state") == "sent" \
            else self.words("apply.held", title=p.title, why=self.applications.why(pid))

    # ── calendar ────────────────────────────────────────────────────────────

    AUTO_TAG = "muninn:auto"

    def schedule_today(self, *, force: bool = False) -> list[ActionRecord]:
        """Blocks time today for the top priorities, in Muninn's own calendar.
        Once a day (force re-plans): blocks Muninn placed earlier that have not
        started are cleared first. Every block is an undoable action."""
        if self.calendar is None or not self.calendar.available():
            return []
        now = self.clock.now()
        today = local_date(now, self.tz).isoformat()
        if not force and not self.store.claim(f"schedule:{today}", now):
            return []
        start = max(now, datetime.combine(local_date(now, self.tz), self.hours.start, local(now, self.tz).tzinfo))
        end = datetime.combine(local_date(now, self.tz), self.hours.end, local(now, self.tz).tzinfo)
        if start >= end:
            return []
        for ev in self.calendar.own_events(start, end):
            if ev.start >= now:
                self.delete_auto_event(ev.id)
        home = self.home_view()
        tasks = [Task(self.words("assembly.send_packet", title=r["title"]), DURATIONS["packet"], f"#packet/{r['id']}") for r in home["ready"]]
        tasks += [Task(q["text"], DURATIONS.get(q["kind"], 30), q["why"]) for q in home["next"]]
        blocks = place(tasks, self.calendar.busy(start, end), start, end)
        out = []
        for b in blocks:
            desc = f"{b.task.note}\n\n{self.words('assembly.scheduled_note')}\n{self.AUTO_TAG}"
            eid = self.calendar.create(f"Muninn · {b.task.title}", b.start, b.end, desc)
            hm = f"{local(b.start, self.tz):%H:%M}–{local(b.end, self.tz):%H:%M}"
            out.append(self.store.log_action(ActionRecord(None, now, "muninn", self.words("assembly.scheduled", hm=hm, title=b.task.title),
                                                           {"kind": "calendar_event", "id": eid})))
        return out

    def delete_auto_event(self, event_id: str) -> None:
        return self.engine.delete_auto_event(event_id)

    # ── mail (Mailroom; these stay so callers need not change) ──────────────

    @property
    def mailboxes(self) -> list[Mailbox]:
        return self.mailroom.mailboxes

    @mailboxes.setter
    def mailboxes(self, boxes: Sequence[Mailbox]) -> None:
        self.mailroom.mailboxes = list(boxes)

    @property
    def triager(self) -> MailTriager | None:
        return self.mailroom.triager

    @triager.setter
    def triager(self, triager: MailTriager | None) -> None:
        self.mailroom.triager = triager

    def triage_mail(self, limit: int = 10) -> int:
        return self.mailroom.triage(limit)

    def read_mail(self, account: str, message_id: str) -> dict[str, Any] | None:
        return self.mailroom.read(account, message_id)

    def save_reply_draft(self, pid: int, body: str) -> bool:
        return self.mailroom.save_reply_draft(pid, body)

    def _alert(self, signal: Signal, now: datetime) -> None:
        return self.engine._alert(signal, now)

    def cloud_login_lost(self, backend: LLMBackend, why: str) -> None:
        """A cloud model's login lapsed: a push (again within a day only if it was cleared), since
        until it is fixed what it would answer fails instead of being answered by another model."""
        self._alert(Signal(key="cloud-login", domain="muninn", kind="cloud_login", priority=1, level=1,
                           title=self.words("assembly.login_lost", label=backend.label),
                           detail=self.words("assembly.login_lost_detail", name=backend.name, why=why, label=backend.label),
                           actions=(backend.login_hint,) if backend.login_hint else (), domain_priority=1), self.clock.now())

    def over_budget(self, spent: float, limit: float) -> None:
        """Today's limit on what the models cost is reached: said once, since until tomorrow what needs a cloud model
        is not done (each kind of work as when no model answers)."""
        self._alert(Signal(key="model-budget", domain="muninn", kind="budget", priority=2, level=1,
                           title=self.words("assembly.over_budget", limit=f"{limit:g}"),
                           detail=self.words("assembly.over_budget_detail", spent=f"{spent:.2f}", limit=f"{limit:g}"),
                           domain_priority=1), self.clock.now())

    def command(self, text: str) -> CommandResult:
        """Something the user typed to Muninn: understood locally, carried out."""
        return self.commands().run(text)

    def apply_plan_edit(self, domain: str, edit: Any) -> Ran | None:
        """Changes a plan the user already said yes to; undoable."""
        return self.plans_actor.edit(domain, edit)

    def target_title(self, domain: str, target: str) -> str:
        return self.engine.target_title(domain, target)

    def describe_plan_edit(self, domain: str, edit: Any) -> str:
        return self.engine.describe_plan_edit(domain, edit)

    def _plan_doc(self, domain: str) -> dict[str, Any]:
        return self.engine._plan_doc(domain)

    def add_feature_request(self, text: str, thread: str | None = None, via: Sequence[str] = ()) -> int:
        return self.requests.add(text, thread, via)

    def feature_requests(self) -> list[dict[str, Any]]:
        return self.requests.open()

    def request_commit(self, rid: int, commit: str) -> bool:
        """The commit that implements request `rid`: it is marked live once the running Muninn includes it."""
        return self.requests.set_commit(rid, commit)

    def check_live(self, locked: Callable[[Callable[[], Any]], Any]) -> int:
        """Requests the running Muninn now includes, or no longer does after a rollback: said in their conversations."""
        return self.requests.check(locked)

    # ── corrections, kept on the evaluation case that caused them ───────────

    def link_case(self, ref: str, case: int | None) -> None:
        return self.engine.link_case(ref, case)

    def case_feedback(self, ref: str, feedback: dict[str, Any]) -> None:
        return self.engine.case_feedback(ref, feedback)

    def replay_notes(self, since: datetime) -> int:
        """Runs what the user typed since then through today's understanding
        again, without acting on it: more evaluation cases from real words."""
        n = 0
        for o in self.store.observations(since, kinds=["note"]):
            if o.source == "pwa" and (text := str(o.payload.get("text") or "").strip()):
                self.commands().understand(text, keep_note=False)
                n += 1
        return n

    # ── facts the user tells Muninn ─────────────────────────────────────────

    def handoffs(self, *, status: str | None = "pending", since: int = 0) -> list[dict[str, Any]]:
        return self.engine.handoffs(status=status, since=since)

    def _add_handoff(self, **fields: Any) -> dict[str, Any]:
        return self.engine._add_handoff(**fields)

    def set_handoff_status(self, hid: int, status: str) -> bool:
        return self.engine.set_handoff_status(hid, status)

    def lease_projects(self, via: Sequence[str]) -> set[str]:
        return self.engine.lease_projects(via)

    def apply_impact(self, fact: str, impact: Any, quote: str | None = None, fyi: bool = True,
                     only_to: set[str] | None = None) -> list[Any]:
        return ImpactApplier(self.clock, self.records, self.handoff_log, self.attr_cfg.sensitive_domains,
                             lambda domain: self._plan(domain, include_unconfirmed=True), self.mark_deadline_done,
                             self.apply_plan_edit, self.tasks, words=self.words).apply(impact, quote, fyi=fyi, only_to=only_to)

    def records_ack(self, project: str, through: int) -> int:
        """A project has taken the records up to cursor `through`."""
        return self.records_out.ack(project, through)

    def project_renamed(self, old: str, new: str) -> None:
        """An idea's sandbox, or the project made of it, goes by a new name: its talks and what was handed to it
        follow the name."""
        if self.leases is not None:
            self.leases.renamed(old, new)
        self.handoff_log.renamed(old, new)

    def talk_ended(self, lease: dict[str, Any], report: str | None) -> None:
        """A deep talk came back (`report`: its handover as it joined the conversation; None when it broke off). What
        follows, in order: what the conversation asked before the talk is past (undoably); its kept state is folded
        again; an idea's experiment is reported. Runs where threads are written; asks no model. A to-do the talk left
        before and its latest handover no longer lists stays as it is: a requester's silence settles nothing (Principal
        Handoff core R4); one it says is done or not needed arrives as its own claim or withdrawal."""
        tid = str(lease["thread"])
        called = self.leases.label(lease["project"]) if self.leases else lease["project"]
        self.gate.run(AskSettled(tid, self.words("assembly.talked_since", called=called)), DecidedBy.INFERRED_SURE)
        self.topic_memory().went_on(tid)
        if report is not None:
            self.idea_reports().returned(lease, report)

    def project_answered(self, asked: dict[str, Any], m: dict[str, Any]) -> None:
        """A project's answer to what the user had Muninn ask it: said in that conversation, kept to that project."""
        project = asked["project"]
        said = (self.words("assembly.closed_unanswered", project=project) if m["type"] == "done"
                else self.words("assembly.answered", project=project, text=m["text"]))
        self.conversation().speak(None, said, [], thread=asked.get("thread"), via=[f"project:{project}"])
        if not self.composer.quiet(self.clock.now()):
            self.channel.send([PushMessage("muninn", 1, title=self.words("assembly.answered_push", project=project))])

    def task_closed(self, t: dict[str, Any]) -> None:
        """A finished task marks the plan deadlines it stands for done too."""
        for link in t.get("links") or []:
            if link.startswith("deadline:") and link[9:] not in self.store.completed():
                self.set_done(link[9:])

    def mark_deadline_done(self, qid: str, why: str = "") -> ActionRecord:
        """The user reported it done: done, and confirmed as a real item; undoable."""
        domain, _, did = qid.partition("/")
        plan = self._plan(domain, include_unconfirmed=True)
        title = next((d.title for d in plan.deadlines if d.id == did), did) if plan else did
        self.set_done(qid)
        self.decide(review_id(domain, "deadline", did), "confirmed")
        for t in self.tasks.open():  # the task standing for it is done as well
            if f"deadline:{qid}" in t["links"]:
                self.tasks.close(t["key"], by="muninn", why=why)
        return self.store.log_action(ActionRecord(None, self.clock.now(), domain, self.words("assembly.done", title=title), {"kind": "deadline_done", "id": qid}))

    def understand_command(self, text: str) -> dict[str, Any] | None:
        return self.commands().understand(text)

    # ── conversations ───────────────────────────────────────────────────────


    def topic_router(self) -> TopicRouter:
        """The actors that can be the topic of a discussion, with what each holds now."""
        return TopicRouter([
            self.plan_review,
            self.packets,
            self.alerts,
            self.ideas,
            Onboarding(lambda: [(p.id, p.title) for p in self._all_plans()], words=self.words),
            TaskActor(self.tasks, lambda project: self._project_domain()(project)),
            self.questions(),
        ])

    def questions(self) -> Questions:
        """Muninn's and the projects' questions to the user, with what Muninn holds now."""
        def confirm_deadline(qid: str) -> None:
            domain, _, did = qid.partition("/")
            self.decide(review_id(domain, "deadline", did), "confirmed")  # it was real; no need to ask about it

        handlers = {
            ActionGate.ASK: lambda c, a: self.gate.answered(c.data, a == c.options[0], c.id),
            "offline_time": self.offline.answered,
            "window_class": self.windows.answered,
            "fact_done": lambda c, a: self.records_actor.deadline_answered(c, a, confirm_deadline),
            "plan_change": self._plan_change_answered,
            "status_from_mail": self.records_actor.status_answered,
            "attribution": self._attribution_answered,
            "replaced": self.tasks.replaced_answered,
        }
        return Questions(self.store, self.pending_confirmations, self.messages, lambda project: self._project_domain()(project),
                         self.understanding.clear, self.windows.question_topic, handlers, self.case_feedback,
                         self.store.actions, self.clock)

    def about(self, kind: str, ref: str) -> dict[str, Any] | None:
        """What the user is discussing, as the model sees it (None when there is no such item)."""
        return self.topic_router().topic(kind, ref)

    # the conversation (Conversation in core/conversation.py)

    def conversation(self) -> Conversation:
        return Conversation(self.store, self.clock, self.commands, self.about, self.topic_memory(),
                            spoke_in=lambda tid: self.leases.left(tid) if self.leases is not None else None, words=self.words)

    def topic_memory(self) -> TopicMemory:
        """What each topic of the one long conversation remembers, and what a round reads of it."""
        return TopicMemory(self.store, self.clock, self.router, lambda: [x for x in self.topics().today() if x["state"] in OPEN],
                           words=self.words)

    def topics(self) -> Topics:
        """Which conversation a sentence belongs to, asked as the routing says."""
        return Topics(self.store, self.clock, LLMDecider(self.router, self.store.set_case_verdict),
                      lambda about: (self.about(about["kind"], str(about["id"])) or {}).get("title"),
                      aka=self.ideas.aka, named=self.ideas.named, words=self.words)

    def opened(self) -> list[str]:
        """The app came into view: what Muninn opened with, if the user was away (SpeakUp.opened)."""
        return self.speak_up().opened()

    def speak_up(self) -> SpeakUp:
        return SpeakUp(self.store, self.clock, self.conversation, self.waiting_asks, self.composer.quiet,
                       lambda title: self.channel.send([PushMessage("muninn", 1, title=title)]), self.last_heard,
                       tz=self.tz, reminders=self.alerts.current, read=self.alerts.mark_read, words=self.words)

    def last_heard(self) -> str | None:
        """When the conversation last went on: what the user said to Muninn, or anything said in a deep talk
        still lent, by either side; now, while a lent session is still at what it was asked."""
        lent = [x for x in self.store.leases() if not x.get("ended") and x["state"] not in ("detaching", "terminal")]
        if any(x.get("answering") for x in lent):
            return self.clock.now().isoformat()
        times = [self.store.get_state("talk:last_user"), *(t for x in lent for t in (x.get("last_user_at"), x.get("last_activity")))]
        return max((t for t in times if t), default=None)

    def waiting_asks(self) -> list[Ask]:
        """What Muninn may bring up in a pause, most pressing first: the entries of what waits that can be answered by
        talking, in the list's own order."""
        return [Ask(w.id, w.says, w.about, thread=w.thread, suggest=w.suggest, via=w.via) for w in self.waiting() if w.speak]

    def confirmation_options(self, cid: int) -> list[str]:
        return self.engine.confirmation_options(cid)

    def execute_command(self, plan: dict[str, Any] | None) -> CommandResult:
        return self.commands().execute(plan)

    # ── places (Whereabouts; these stay so callers need not change) ───────

    def places(self) -> list[Place]:
        return self.whereabouts.places()

    def stays_between(self, start: datetime, until: datetime) -> list[Stay]:
        return self.whereabouts.stays_between(start, until)

    def day_stays(self, day: date | None = None) -> list[Stay]:
        return self.whereabouts.day_stays(day)

    def where_and_what(self, start: datetime, until: datetime) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
        return self.whereabouts.where_and_what(start, until)

    def places_line(self, day: date | None = None) -> str | None:
        return self.whereabouts.places_line(day)

    # ── reflection ──────────────────────────────────────────────────────────

    # reflection (Reflection in core/reflect.py; these stay so callers need not change)

    @property
    def reflector(self) -> Reflector | None:
        return self.reflection_desk.reflector

    @reflector.setter
    def reflector(self, reflector: Reflector | None) -> None:
        self.reflection_desk.reflector = reflector

    @property
    def transcripts(self) -> TranscriptSource | None:
        return self.reflection_desk.transcripts

    @transcripts.setter
    def transcripts(self, transcripts: TranscriptSource | None) -> None:
        self.reflection_desk.transcripts = transcripts

    def collect_conversations(self) -> int:
        return self.reflection_desk.collect_conversations()

    def reflect_digest(self, budget_s: float = 3600, reserve_s: float = 180) -> tuple[int, int]:
        return self.reflection_desk.digest(budget_s, reserve_s)

    def week_digests(self, week: str) -> dict[str, Any]:
        return self.reflection_desk.week_digests(week)

    def _project_domain(self) -> Callable[[str | None], str | None]:
        return self.reflection_desk.project_domains()

    def week_facts(self, since: datetime, until: datetime) -> WeekFacts:
        return self.reflection_desk.week_facts(since, until)

    def reflection(self, *, preview: bool = False) -> list[InboxItem]:
        return self.reflection_desk.weekly(preview=preview)

    def match_todos(self) -> dict[str, str]:
        """Asks which of the projects' to-dos are the same task as Muninn's own
        open items or as each other (only when the lists changed)."""
        items = self.open_items.items()
        muninn = [{"id": i.id, "text": i.text} for i in items if not i.id.startswith("todo:")]  # Muninn's own and the tasks
        todos = [{"id": i.id[5:], "project": i.owner, "text": i.text} for i in items if i.id.startswith("todo:")]
        return self.todo_matcher.match(muninn, todos) if todos else {}

    def external_todos(self) -> dict[str, dict[str, list[str]]]:
        return self.engine.external_todos()

    # ── inbound from the PWA ────────────────────────────────────────────────

    def today_view(self) -> dict[str, Any]:
        return self.views().today()

    def add_note(self, text: str, domain: str | None) -> Observation | None:
        return self.engine.add_note(text, domain)

    def correct_hours(self, goal: str, hours: float, day: date | None, note: str = "") -> Observation | None:
        return self.records_actor.correct_hours(goal, hours, day, note)

    def set_status(self, domain: str, target: str, status: str) -> int:
        return self.records_actor.set_status(domain, target, status)

    def undo_status(self, change_id: int) -> bool:
        return self.records_actor.undo_status(change_id)

    def set_done(self, qualified_id: str, done: bool = True) -> None:
        self.records_actor.set_done(qualified_id, done)

    def answer(self, cid: int, answer: str) -> Confirmation | None:
        return self.questions().answer(cid, answer)

    def _plan_change_answered(self, c: Confirmation, answer: str) -> None:
        return self.engine._plan_change_answered(c, answer)

    def _attribution_answered(self, c: Confirmation, answer: str) -> None:
        return self.engine._attribution_answered(c, answer)
