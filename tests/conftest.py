from __future__ import annotations

import os

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from starlette.testclient import TestClient

from muninn.languages import language

# a config without `language` follows the machine's locale: tests run as on a machine set to Chinese, wherever they run
for _var in ("LC_ALL", "LC_MESSAGES"):
    os.environ.pop(_var, None)
os.environ["LANG"] = "zh_TW.UTF-8"
from muninn.adapters.channels.webpush import VapidKeys
from muninn.adapters.store_sqlite import SqliteStore
from muninn.adapters.web.app import create_app
from muninn.adapters.web.devices import DeviceRegistry
from muninn.core.composer import Composer, ComposerConfig
from muninn.core.drift import DeadlineRule, GoalBehindRule, IdleDomainRule, LateNightRule, OverdueActionRule
from muninn.core.assembly import Muninn
from muninn.core.plan_base import AttributionConfig
from muninn.core.llm_router import LLMRouter
from muninn.core.metrics import default_metrics
from muninn.core.models import DomainPlan
from muninn.core.ports import TEXT_ONLY, Capabilities
from muninn.core.plan import parse_plan
from muninn.core.project_messages import ProjectMessages

TZ = "Asia/Taipei"
ZH = language("zh-TW")  # the language the tests' Muninn speaks, for what they build beside the harness
TPE = ZoneInfo(TZ)


def at(y, m, d, hh=12, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=TPE)


class FakeClock:
    def __init__(self, now: datetime):
        self.t = now

    def now(self) -> datetime:
        return self.t


class FakeChannel:
    def __init__(self):
        self.sent = []

    def send(self, messages):
        self.sent.append(list(messages))
        return len(messages)


@dataclass
class RecordingLLM:
    name: str
    is_local: bool
    up: bool = True
    label: str = "模型"
    login_hint: str = ""
    reply: str = "ok"
    calls: list[tuple[str, str]] = field(default_factory=list)
    capabilities: Capabilities = TEXT_ONLY

    def available(self) -> bool:
        return self.up

    def check(self) -> str:
        if not self.up:
            raise RuntimeError("not reachable")
        return "reachable"

    def complete(self, system: str, prompt: str, *, json_mode: bool = False) -> str:
        self.calls.append((system, prompt))
        return self.reply


class MemoryPlanRepo:
    def __init__(self, plans: list[DomainPlan]):
        self.plans = {p.id: p for p in plans}
        self.archived: dict[str, DomainPlan] = {}

    def load(self):
        return list(self.plans.values())

    def set_tracker_status(self, domain, target, status, applied, *, replace_applied=False):
        p = self.plans[domain]
        rows = list(p.tracker)
        i = next(i for i, r in enumerate(rows) if r.target == target)
        before = {"status": rows[i].status, "applied": rows[i].applied.isoformat() if rows[i].applied else None}
        new_applied = applied if replace_applied else (rows[i].applied or applied)
        rows[i] = replace(rows[i], status=status, applied=new_applied)
        self.plans[domain] = replace(p, tracker=tuple(rows))
        return before

    def create(self, doc):
        from muninn.core.plan import parse_plan

        plan = parse_plan(doc, [], TZ)
        if plan.id in self.plans or plan.id in self.archived:
            raise ValueError(f"there is already a domain {plan.id}")
        self.plans[plan.id] = plan
        return plan.id

    def archive(self, domain):
        self.archived[domain] = self.plans.pop(domain)

    def restore(self, domain):
        if domain in self.plans:
            raise ValueError(f"there is already a domain {domain}")
        self.plans[domain] = self.archived.pop(domain)


# A synthetic plan with the same schema as real plans. CANARY strings mark
# human text that must never leave the machine.
CANARY = "CANARY-7f3a"

JOBS_RAW: dict[str, Any] = {
    "domain": {"id": "jobs", "title": "Job search", "priority": 1, "sensitivity_default": "personal"},
    "timezone": TZ,
    "pivot": {"date": "2026-12-01", "if": f"no offer {CANARY}", "then": f"switch plans {CANARY}"},
    "filters": [f"only full-time roles {CANARY}"],
    "goals": [
        {"id": "apply", "title": "Weekly applications", "metric": "applications_submitted", "target": 4, "period": "week", "priority": 1,
         "if_behind": ["Apply to the top open target today"]},
        {"id": "golang", "title": "Go practice", "metric": "hours", "target": 6, "period": "week", "priority": 2,
         "if_behind": ["One hour of Go concurrency tonight"]},
    ],
    "deadlines": [
        {"id": "first-batch", "title": "First batch", "due": "2026-10-24", "lead_days": 2, "goal": "apply", "priority": 1,
         "done_when": {"targets": ["acme", "globex"], "status_at_least": "applied"}},
        {"id": "call-bank", "title": "Call the bank", "due": "2026-10-31", "lead_days": 3, "priority": 1, "done_when": "manual"},
    ],
    "targets": [
        {"id": "acme", "company": "Acme", "role": "Staff Engineer", "priority": 1, "url": "https://example.com/acme", "notes": f"gap is Go {CANARY}"},
        {"id": "globex", "company": "Globex", "role": "Backend Engineer", "priority": 1, "notes": f"ask about level {CANARY}"},
    ],
    "decision_tree": [{"when": f"offer arrives {CANARY}", "then": f"compare {CANARY}"}],
}
JOBS_TRACKER = [
    {"target": "acme", "status": "todo", "next_action": "Apply; ask salary band", "next_action_due": "2026-10-24"},
    {"target": "globex", "status": "todo", "next_action": "Apply", "next_action_due": "2026-10-24"},
]
HEALTH_RAW = {
    "domain": {"id": "health", "title": "Health", "priority": 2, "sensitivity_default": "personal"},
    "timezone": TZ,
    "goals": [
        {"id": "no-late-work", "title": "No work after 23:30", "metric": "late_activity", "cutoff": "23:30", "target": 0, "period": "day", "priority": 1,
         "if_behind": ["Wrap up now"]},
    ],
}
LEARNING_RAW = {"domain": {"id": "learning", "title": "Learning", "priority": 4, "sensitivity_default": "personal"}, "timezone": TZ, "goals": []}
WORK_RAW = {"domain": {"id": "work", "title": "Work", "priority": 6, "sensitivity_default": "sensitive"}, "timezone": TZ, "goals": []}


def jobs_plan() -> DomainPlan:
    return parse_plan(JOBS_RAW, JOBS_TRACKER, TZ)


ATTRIBUTION = {
    "sensitive_domains": ["work"],
    "repos": {"resume": "jobs", "work-api": "work", "go-katas": {"domain": "jobs", "goal": "golang"}},
    "languages": {"go": "jobs/golang"},
}


@dataclass
class Harness:
    engine: Engine
    store: SqliteStore
    clock: FakeClock
    channel: FakeChannel
    local: RecordingLLM
    cloud: RecordingLLM
    plans: MemoryPlanRepo


def make_harness(now: datetime, plans: list[DomainPlan] | None = None, sources=(), composer_cfg: ComposerConfig | None = None,
                 confirm_all: bool = True, lang: str = "zh-TW") -> Harness:
    store = SqliteStore(":memory:")
    clock = FakeClock(now)
    channel = FakeChannel()
    local = RecordingLLM("local", True, reply='{"domain": "jobs"}')
    cloud = RecordingLLM("cloud", False, reply="Cloud wording.")
    if plans is None:
        plans = [jobs_plan(), parse_plan(HEALTH_RAW, (), TZ), parse_plan(LEARNING_RAW, (), TZ), parse_plan(WORK_RAW, (), TZ)]
    repo = MemoryPlanRepo(plans)
    from muninn.core.evals import CaseRecorder

    router = LLMRouter([cloud, local], ATTRIBUTION["sensitive_domains"], recorder=CaseRecorder(store, clock))
    from muninn.core.classify import LocalDomainClassifier

    engine = Muninn(
        words=language(lang),
        store=store,
        plans=repo,
        sources=list(sources),
        channel=channel,
        router=router,
        classifier=LocalDomainClassifier(router),
        clock=clock,
        metrics=default_metrics(),
        rules=[GoalBehindRule(), DeadlineRule(), OverdueActionRule(), LateNightRule(tz=TZ), IdleDomainRule(3)],
        composer=Composer(store, composer_cfg or ComposerConfig(tz=TZ), words=language(lang)),
        attribution=AttributionConfig(rules=ATTRIBUTION, sensitive_domains=frozenset(ATTRIBUTION["sensitive_domains"])),
        tz=TZ,
    )
    if confirm_all:  # most tests are about behaviour on goals the user already agreed to
        from muninn.core.review import review_id

        store.set_state("plan_review", {
            review_id(p.id, kind, x.id): "confirmed"
            for p in plans for kind, items in (("goal", p.goals), ("deadline", p.deadlines)) for x in items
        })
    return Harness(engine, store, clock, channel, local, cloud, repo)


@pytest.fixture(autouse=True)
def built_web_app(monkeypatch):
    """The tests run whether or not the TypeScript was built here (CI builds it first; a fresh clone may not have)."""
    monkeypatch.setattr("muninn.adapters.web.app.unbuilt", lambda: [])


@pytest.fixture
def harness():
    # Tuesday 2026-10-20, 14:00 local
    return make_harness(at(2026, 10, 20, 14))


@pytest.fixture
def web(tmp_path):
    h = make_harness(at(2026, 10, 22, 10))
    keys = VapidKeys(tmp_path / "secrets" / "vapid.pem")
    keys.ensure()
    devices = DeviceRegistry(tmp_path / "dev.db")
    messages = ProjectMessages(h.store, h.clock, h.engine.handoff_log, words=ZH)
    from muninn.main import LOCATION_APPS

    client = TestClient(create_app(h.engine, keys, "tok", devices, messages=messages, location_apps=LOCATION_APPS), base_url="https://testserver")
    return h, client, devices
