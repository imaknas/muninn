"""Composition root and CLI. This is the only module that picks concrete adapters.

    muninn tick | morning | evening | weekly   scheduled, idempotent
    muninn serve                               web server (PWA + ingest API)
    muninn init                                create keys and tokens (idempotent)
    muninn pair [--name NAME]                  print a one-time pairing code
    muninn devices [--revoke ID]               list or revoke paired devices
    muninn status                              print today's view as JSON
"""

from __future__ import annotations

import argparse
from importlib import resources
import json
import threading
import logging
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
from ruamel.yaml import YAML

from muninn.adapters.channels.webpush import ConsoleChannel, VapidKeys, WebPushChannel
from muninn.adapters.documents import FileDocuments
from muninn.adapters.tailscale import Tailscale
from muninn.adapters.uploads import FileUploads
from muninn.core.apply import FormFiller, Sites
from muninn.adapters.lease_runner import HttpSandboxes, HttpSessionRunner
from muninn.adapters.running_version import GitVersion
from muninn.adapters.postings import default_fetcher
from muninn.adapters.clock import SystemClock
from muninn.adapters.llm.anthropic_api import AnthropicApiProvider
from muninn.adapters.llm.claude_cli import ClaudeCliProvider
from muninn.adapters.llm.ollama import OllamaBackend, OllamaProvider
from muninn.adapters.llm.openai_compat import OpenAICompatProvider
from muninn.adapters.llm.reply_check import ReplyCheck, schemas
from muninn.adapters.google.calendar import GoogleCalendar
from muninn.adapters.google.gmail import GmailMailbox
from muninn.adapters.calendar_merge import CalendarWithExtraBusy
from muninn.adapters.google.oauth import GoogleOAuth
from muninn.adapters.microsoft.graph import GraphBusy, GraphMailbox, MicrosoftOAuth
from muninn.adapters.plans_yaml import YamlPlanRepo
from muninn.adapters.local_projects import LocalProjects
from muninn.adapters.project_records import FileProjectRecords
from muninn.adapters.sources.claude_code import ClaudeCodeSource
from muninn.adapters.sources.conversations import TranscriptTextSource
from muninn.adapters.sources.git import GitSource
from muninn.adapters.store_sqlite import SqliteStore, StoreTooNew
from muninn.core.redact import redact
from muninn.package import CONTRACTS, version
from muninn.form_filling import FormFilling
from muninn.setup_flow import Setup
from muninn.core.language import Language
from muninn.languages import CODES, language
from muninn.adapters import installs, scheduler
from muninn.core import eval_suite
from muninn.core import commands as command_prompt, facts as facts_prompt
from muninn.core.understanding import WORK_SYSTEM
from muninn.adapters.web.devices import DeviceRegistry
from muninn.config import BadSetting, Config, MissingSecret
from muninn.llm_config import LLMSetup, ProviderContext, llm_setup
from muninn.core.cases import CaseFeedback
from muninn.core.classify import LocalDomainClassifier
from muninn.core.composer import Composer, ComposerConfig
from muninn.core.drift import DeadlineRule, GoalBehindRule, IdleDomainRule, LateNightRule, OverdueActionRule
from muninn.core.assembly import Muninn
from muninn.core.plan_base import AttributionConfig
from muninn.core.decide import LLMDecider
from muninn.core import prompt_check
from muninn.core.evals import CaseRecorder, Evaluator, ModelJudge, coverage, decisions
from muninn.core.handoffs import Handoffs
from muninn.adapters.sources.location.owntracks import OwnTracks
from muninn.core import handoff_records, ph
from muninn.core.understanding import Understanding, WorkCheck
from muninn.core.llm_router import CloudFirst, LLMRouter, PrivacyFirst
from muninn.core.task_models import GROUPS, ModelChoice, described
from muninn.core.metrics import default_metrics
from muninn.core.mail import MailTriager
from muninn.core.prepare import PacketPreparer
from muninn.core.command_trust import CommandTrust
from muninn.core.lease import ChannelLease, PermissionDesk
from muninn.core.ports import LLMBackend, RunningVersion, SessionRunner, UnknownVersion
from muninn.core.project_messages import ProjectMessages
from muninn.core.actions import ActionGate
from muninn.core.tasks import TaskRegistry
from muninn.core.todo_match import ModelTaskMatch
from muninn.core.budget import DailyBudget, Metered
from muninn.core.reflect import Reflector
from muninn.core.window_classify import WindowClassifier
from muninn.core.timewin import ActiveHours, iso_week

log = logging.getLogger("muninn")
# the tracking apps a phone can send its location from: one implementation each (core/location.py)
LOCATION_APPS = (OwnTracks(),)
CONTRACT_V = 1  # the version of the JSON this CLI hands to other systems (contracts/*.v1.schema.json)


def running_version(code: Path, data_dir: Path, words: Language) -> RunningVersion:
    """The version this code runs as: read with git from the clone it runs from (the deploy log beside the data); an
    install without git says it cannot tell, and nothing is taken to be live."""
    version = GitVersion.at(code, data_dir / "deploys.log")
    if version is None:
        # an installed package, not a clone: normal, and nothing to put right (said only when asked for detail)
        log.debug("no git checkout at %s: requests for Muninn are not marked live by themselves", code)
        return UnknownVersion(words("main.no_git"))
    return version


@dataclass
class App:
    config: Config
    engine: Engine
    keys: VapidKeys
    devices: DeviceRegistry
    documents: FileDocuments
    connections: dict
    messages: ProjectMessages
    lease_runner: SessionRunner | None = None
    permissions: PermissionDesk | None = None
    # (name, check) pairs the running server answers on /api/smoke; a check returns a detail or raises
    smoke: list = field(default_factory=list)
    problems: list = field(default_factory=list)  # settings that cannot work as written (model_problems)
    off: list = field(default_factory=list)  # features this installation does without, and why (features_off)


def work_patterns(attr: dict, sensitive: frozenset[str]) -> list[str]:
    """Regexes that mark text as work without asking a model: the work
    domains' window rules and the names of their repos."""
    out = [str(w["title"]) for w in attr.get("windows") or [] if w.get("domain") in sensitive and w.get("title")]
    for repo, v in (attr.get("repos") or {}).items():
        if (v if isinstance(v, str) else (v or {}).get("domain")) in sensitive:
            out.append(rf"(?i)\b{re.escape(str(repo))}\b")
    return out


def repo_domains(attr: dict) -> dict[str, str]:
    out = {}
    for repo, v in (attr.get("repos") or {}).items():
        domain = v if isinstance(v, str) else (v or {}).get("domain")
        if domain:
            out[str(repo)] = str(domain)
    return out


def application_sites() -> Sites:
    """The sites application forms are filled on: Greenhouse, when a browser to fill them in is installed (the
    `autofill` extra); none otherwise, and packets are applied to by hand."""
    from muninn.adapters.apply import browser

    if not browser.available():
        return Sites()
    from muninn.adapters.apply.greenhouse import Greenhouse

    def get_json(url: str) -> tuple[int, Any]:
        try:
            r = httpx.get(url, timeout=20, follow_redirects=True)
            return r.status_code, r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as e:
            log.info("cannot read %s: %s", url, e)
            return 0, None

    return Sites([Greenhouse(get_json, browser.browse)])


def form_documents(documents: FileDocuments, out_dir: Path) -> Any:
    """The résumés as forms take them: a web page one printed to PDF."""
    from muninn.adapters.apply.browser import PdfDocuments

    return PdfDocuments(documents, out_dir)


def keep_picture(uploads: FileUploads, name: str, data: bytes) -> str:
    """A picture of a filled form, kept where the app shows uploads from; "" when it could not be kept."""
    try:
        return str(uploads.save(name, "image/jpeg", data)["id"])
    except (OSError, ValueError) as e:
        log.warning("picture of %s not kept: %s", name, e)
        return ""


def build(config: Config, *, dry_run: bool = False) -> App:
    cfg = config
    cfg.port  # noqa: B018  (a port it cannot listen on stops `init` and `check` too, before a setup link names it)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(cfg.data_dir, 0o700)
    store = SqliteStore(cfg.db_path)
    keys = VapidKeys(cfg.secrets_dir / "vapid_private.pem")

    attr = cfg.attribution
    sensitive = frozenset(attr.get("sensitive_domains") or ())
    extra = {str(k): str(v) for k, v in (attr.get("domains") or {}).items()}

    llm = cfg["llm"]
    side = providers_of(cfg, meter_into(store, cfg.tz))  # None: no model at all
    cloud_side = side if side is not None and side.cloud else None  # None: no cloud model
    cloud = cloud_side is not None
    # one switch for where models run: "cloud" (everything, no local model) or "privacy" (see llm_router)
    cloud_first = llm.get("routing") == "cloud" and cloud
    policy = CloudFirst() if cloud_first else PrivacyFirst()
    recorder = CaseRecorder(store, SystemClock(cfg.tz))  # every request and reply, for evaluating models later
    limit = daily_limit(cfg)
    # a daily limit on what the cloud models cost, when one is set: counted from the tokens every reply took
    budget = DailyBudget(limit, side.setup.prices() if side else {}, store.usage_since, SystemClock(cfg.tz),
                         store.get_state, store.set_state, lambda spent, most: engine.over_budget(spent, most)) if limit else None
    over_budget = budget.over if budget else None
    check_reply = ReplyCheck(schemas(REGISTRY.parent))  # each decision point's reply, held to its declared shape
    # the local model, when one is named: none at all is a choice (cloud only, or no model yet), not a fault
    local_side = side.local() if side is not None else None
    locals_ = [local_side] if local_side is not None else []
    models = cloud_side.models if cloud_side else None

    def cloud_backend(choice: ModelChoice, timeout: float = 120.0):  # that provider's backend for that model
        assert cloud_side is not None
        return cloud_side.backend(choice, timeout)

    def cloud_for(default: ModelChoice, timeout: float = 120.0):
        """Each task's provider and model from llm.models; the router's own for tasks in no group."""
        assert models is not None
        return lambda task: [cloud_backend(models.model(task) or default, timeout)]

    cloud_model = cloud_side.setup.choice("fast") if cloud_side else None  # everything not the user's own words
    def login_lost(backend, why: str) -> None:
        engine.cloud_login_lost(backend, why)  # the front object, built below; only called once requests run

    # every cloud model in use is known to it, so what each wrote is told by its provider's name
    router = LLMRouter([*locals_, *(cloud_backend(c) for c in sorted(cloud_side.in_use() if cloud_side else ()))], sensitive, policy, recorder,
                       cloud_for=cloud_for(cloud_model) if cloud else None, on_login_lost=login_lost, check_reply=check_reply,
                       over_budget=over_budget)
    reflect_timeout = float(llm.get("reflect_timeout", 180))
    # conversation digests: optionally a faster local model than the default
    reflect_locals = [side.local(llm.get("reflect_model"), reflect_timeout)] if local_side is not None and side is not None else []
    reflect_cloud = max(reflect_timeout, 300)
    reflect_router = LLMRouter([*([cloud_backend(cloud_model, reflect_cloud)] if cloud_first else []), *reflect_locals], sensitive, policy,
                               recorder, cloud_for=cloud_for(cloud_model, reflect_cloud) if cloud_first else None,
                               on_login_lost=login_lost, check_reply=check_reply,
                       over_budget=over_budget)

    plan_repo = YamlPlanRepo(cfg.domains_dir, cfg.tz)
    understanding = Understanding(router)
    if cloud_side is not None and (cloud_first or cfg["cloud_understanding"]):
        understanding_model = cloud_side.setup.choice("standard")  # the user's own words
        understanding_router = LLMRouter([cloud_backend(understanding_model), *locals_], sensitive, policy, recorder,
                                         cloud_for=cloud_for(understanding_model), on_login_lost=login_lost, check_reply=check_reply,
                       over_budget=over_budget)
        # privacy-first: a local work check decides what the cloud may read; cloud-first needs none
        gate = None if cloud_first else WorkCheck(router, sensitive, lambda: [(p.id, p.title) for p in plan_repo.load()],
                                                  work_patterns(attr, sensitive), repo_domains(attr))
        understanding = Understanding(understanding_router, gate)

    ws = cfg.paths("workspace_roots")
    sources = [
        GitSource(ws, cfg.git_authors(), exclude=cfg["git_exclude"]),
        ClaudeCodeSource(cfg.paths("transcript_roots"), ws),
    ]
    quiet_start, quiet_end = cfg.quiet_hours
    night_start, night_end = cfg.late_night
    active_start, active_end = cfg.active_hours
    composer_cfg = ComposerConfig(
        tz=cfg.tz,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
        daily_push_cap=int(cfg["daily_push_cap"]),
        interrupt_max_priority=int(cfg.raw.get("interrupt_max_priority", 3)),
        escalation_level=int(cfg.raw.get("escalation_level", 3)),
        push_titles=cfg.raw.get("push_detail", "titles") != "counts",
        **({"tier_quota": {str(k): int(v) for k, v in cfg.raw["tier_quota"].items()}} if cfg.raw.get("tier_quota") else {}),
    )
    channel = ConsoleChannel() if dry_run else WebPushChannel(store, keys, cfg.vapid_subject)
    documents = FileDocuments(cfg.paths("documents"))
    def callback(provider: str) -> str | None:
        return f"{cfg.public_url.rstrip('/')}/oauth/{provider}/callback" if cfg.public_url else None

    sec = cfg.secrets_dir
    google = GoogleOAuth(sec / "google_client.json", sec / "google_token.json", sec / "google_state.json", callback("google"))
    microsoft = MicrosoftOAuth(sec / "microsoft_client.json", sec / "microsoft_token.json", sec / "microsoft_state.json",
                               callback("microsoft"))
    mailboxes = []
    for a in cfg["mail_accounts"]:
        provider, name, kind = a.get("provider", "gmail"), str(a.get("name", "personal")), str(a.get("kind", "personal"))
        if provider == "gmail" and google.configured:
            mailboxes.append(GmailMailbox(google, name=name, kind=kind))
        elif provider == "outlook" and microsoft.configured:
            mailboxes.append(GraphMailbox(microsoft, name=name, kind=kind))
    calendar = None
    if google.configured:
        work_busy = [GraphBusy(microsoft)] if microsoft.configured else []
        calendar = CalendarWithExtraBusy(GoogleCalendar(google, str(cfg["calendar_name"])), work_busy)
    preparer = PacketPreparer(router, default_fetcher(), documents, decider=LLMDecider(router, store.set_case_verdict),
                              cloud_drafts=bool(cfg["cloud_drafts"]))
    sites, uploads = application_sites(), FileUploads(cfg.data_dir / "uploads", words=language(cfg.language))
    cases = CaseFeedback(store, SystemClock(cfg.tz))
    handoffs = Handoffs(store, SystemClock(cfg.tz), cases)
    words = language(cfg.language)  # the language Muninn speaks to the user in
    # what only the user can do; its duplicates are judged by the understanding model
    # the gate that actions go through; it acts on the engine, built below
    gate = ActionGate(store, SystemClock(cfg.tz), lambda: engine, words=words)
    tasks = TaskRegistry(store, SystemClock(cfg.tz), handoffs, ModelTaskMatch(understanding.router), gate,
                         on_close=lambda t: engine.task_closed(t), words=words)
    messages = ProjectMessages(store, SystemClock(cfg.tz), handoffs, tasks, projects=lambda: engine.records.projects(),
                               answered=lambda asked, m: engine.project_answered(asked, m), words=words)
    engine = Muninn(
        words=words,
        store=store,
        plans=plan_repo,
        sources=sources,
        channel=channel,
        router=router,
        classifier=LocalDomainClassifier(router),
        clock=SystemClock(cfg.tz),
        metrics=default_metrics(),
        rules=[
            GoalBehindRule(),
            DeadlineRule(),
            OverdueActionRule(),
            LateNightRule(start=night_start, end=night_end, tz=cfg.tz),
            IdleDomainRule(default_days=cfg["idle_alert_days"]),
        ],
        composer=Composer(store, composer_cfg, words=words),
        attribution=AttributionConfig(rules=attr, sensitive_domains=sensitive, extra_domains=extra),
        hours=ActiveHours(active_start, active_end),
        tz=cfg.tz,
        preparer=preparer,
        prepare_per_tick=int(cfg["prepare_per_tick"]),
        sites=sites,
        form_filler=FormFiller(router) if sites else None,
        documents=form_documents(documents, cfg.data_dir / "applications") if sites else documents,
        keep_picture=lambda name, data: keep_picture(uploads, name, data),
        transcripts=TranscriptTextSource(cfg.paths("transcript_roots"), ws) if cfg["reflect"] else None,
        reflector=Reflector(store, reflect_router, SystemClock(cfg.tz)) if cfg["reflect"] else None,
        calendar=calendar,
        mailboxes=mailboxes,
        triager=MailTriager(router, cloud_mail=bool(cfg["cloud_mail"])),
        connections=[google, microsoft],
        window_classifier=WindowClassifier(router),
        understanding=understanding,
        cases=cases,
        handoffs=handoffs,
        messages=messages,
        tasks=tasks,
        gate=gate,
        owner_label=str(cfg["owner_label"]),
        parties=handoff_records.parties_from(cfg["principal_id"], str(cfg["owner_label"])),
        ph_extensions=ph.extensions(CONTRACTS / "vendor" / "principal-handoff", CONTRACTS / "vendor" / "principal-handoff-0.4"),
        running=running_version(Path(__file__).resolve().parents[2], cfg.data_dir, words),
        **project_side(cfg, words),
    )
    # lending a conversation to a project's session, when a session runner is configured
    lease_runner = permissions = None
    if cfg["lease_runner_url"] and cfg["lease_runner_token_file"]:
        lease_runner = HttpSessionRunner(str(cfg["lease_runner_url"]), str(cfg["lease_runner_token_file"]))
        titles = composer_cfg.push_titles  # the push_detail setting applies to these pushes too
        engine.leases = ChannelLease(store, SystemClock(cfg.tz), lease_runner, channel, engine.composer.quiet,
                                     lambda tid: engine.conversation().thread(tid),
                                     lambda tid, change: engine.conversation().update_thread(tid, change), push_titles=titles,
                                     share_uncleared=cloud_first,
                                     ended=lambda lease, report: engine.talk_ended(lease, report),
                                     label=lambda project: engine.ideas.label_of(project), words=words)
        permissions = PermissionDesk(store, SystemClock(cfg.tz), channel, engine.composer.quiet, engine.leases,
                                     push_titles=titles,
                                     trust=CommandTrust(store, lambda: store.permission_requests(pending=False), words=words),
                                     words=words)
        engine.permissions = permissions
    # every model a request may reach (one check per model): no local one under cloud routing
    in_use = cloud_side.in_use() if cloud_side else set()
    smoke = smoke_checks(store, engine, [*([] if cloud_first else locals_), *(cloud_backend(m) for m in sorted(in_use))], google, lease_runner,
                         secrets=lambda: require_secrets(cfg, keys))
    have = Present(runner=lease_runner is not None, google=google.configured, microsoft=microsoft.configured,
                   mail_accounts=tuple(str(a.get("provider", "gmail")) for a in cfg["mail_accounts"]), mailboxes=len(mailboxes),
                   reflect=bool(cfg["reflect"]), reflect_model=side is None or bool(reflect_router.backends()),
                   form_filling=bool(sites), project_folders=bool(ws), transcripts=bool(cfg.paths("transcript_roots")),
                   model=side is not None)
    return App(cfg, engine, keys, DeviceRegistry(cfg.db_path), documents, {"google": google, "microsoft": microsoft}, messages,
               lease_runner, permissions, smoke,
               model_problems(llm, cloud, local_side is not None, words=words) + budget_problems(cfg, side, words=words),
               features_off(have, words=words))


def model_problems(llm: dict, cloud: bool, local: bool, *, words: Language) -> list[str]:
    """Model settings that cannot work as written, said so instead of quietly doing something else. No model at all
    is not one of them: Muninn then keeps its rules and reminders, and says it has no model to understand with."""
    if llm.get("routing") == "cloud" and not cloud:
        return [words("main.cloud_without_cloud")]
    if llm.get("routing") != "cloud" and cloud and not local:
        return [words("main.privacy_without_local")]
    return []


@dataclass(frozen=True)
class Present:
    """What this installation has, as build() found it: what features_off reads."""
    runner: bool  # a session runner for deep talks
    google: bool  # the Google client (calendar, Gmail)
    microsoft: bool  # the Microsoft client (Outlook mail, work calendar)
    mail_accounts: tuple[str, ...]  # the providers config's mail accounts name
    mailboxes: int  # those of them that can be read
    reflect: bool  # reflection is on in config
    reflect_model: bool  # a model may read the conversations for it
    form_filling: bool  # a browser to fill application forms
    project_folders: bool  # workspace_roots
    transcripts: bool  # transcript_roots
    model: bool = True  # any model to understand what the user says


def features_off(have: Present, *, words: Language) -> list[dict[str, str]]:
    """The features this installation does without, each with why and what turns it on, in the order the user would
    look for them; with no model at all, that comes first (what else needs a model is not listed one by one)."""
    off = []

    def say(feature: str, why: str, **params: str) -> None:
        off.append({"feature": words(f"main.off_{feature}"), "why": words(f"main.why_{why}", **params)})

    if not have.model:
        say("understanding", "no_model")

    if not have.runner:
        say("deep_talks", "no_runner")
    if not have.google:
        say("calendar", "no_google")
    if not have.mail_accounts:
        say("mail", "no_mail_accounts")
    elif not have.mailboxes:
        say("mail", "no_mail_client", clients=words.join(sorted({"secrets/google_client.json" if a == "gmail" else
                                                                     "secrets/microsoft_client.json" for a in have.mail_accounts})))
    if not have.reflect:
        say("reflection", "reflect_false")
    elif not have.reflect_model:
        say("reflection", "reflect_privacy")
    if not have.form_filling:
        say("form_filling", "no_browser")
    if not have.project_folders:
        say("git_activity", "no_workspace_roots")
    if not have.transcripts:
        say("session_activity", "no_transcript_roots")
    return off


def daily_limit(cfg: Config) -> float | None:
    """budget.daily_usd, as a sum above 0; None when unset or not one (budget_problems says which)."""
    limit = (cfg["budget"] or {}).get("daily_usd")
    try:
        return float(limit) if limit is not None and float(limit) > 0 else None
    except (TypeError, ValueError):
        return None


def budget_problems(cfg: Config, side: "Providers | None", *, words: Language) -> list[str]:
    """A daily limit that could not count what it limits: a cloud model in use whose replies do not say what they took,
    or that config gives no price for, would be asked past the limit without anything saying so."""
    limit = (cfg["budget"] or {}).get("daily_usd")
    if limit is None:
        return []
    if daily_limit(cfg) is None:
        return [words("main.budget_not_a_sum", limit=limit)]
    if side is None or not side.cloud:
        return []
    out, prices = [], side.setup.prices()
    for c in sorted(side.in_use()):
        spec = side.setup.providers[c.provider]
        if not IMPLS[spec.impl].counts_tokens:
            out.append(words("main.budget_uncounted", model=f"{c.provider}:{c.model}"))
        elif f"{c.provider}:{c.model}" not in prices:
            out.append(words("main.budget_unpriced", model=f"{c.provider}:{c.model}", where=f"llm.providers.{c.provider}.prices.{c.model}"))
    return out


def project_side(cfg: Config, words: Language) -> dict[str, Any]:
    """Where the projects live, as config says: plain folders Muninn reads and writes itself, or
    another system that keeps the records, posts its messages and pulls the handoffs."""
    side = cfg["projects"] or {}
    if side.get("side", "external") == "local":
        local = LocalProjects(side.get("folders") or [], side.get("sandbox_root"), words=words)
        return {"records": local, "project_inbox": local, "handoff_outbox": local, "sandboxes": local}
    if side.get("side", "external") != "external":
        raise ValueError(f"projects.side: {side.get('side')!r}; known: external, local")
    external: dict[str, Any] = {"records": FileProjectRecords(cfg.raw.get("external_todos"), cfg.raw.get("external_todo_owners") or {},
                                                              cfg.raw.get("project_states"), cfg.raw.get("weekly_stats"))}
    if cfg["lease_runner_url"] and cfg["lease_runner_token_file"]:  # the session runner opens the idea sandboxes too
        external["sandboxes"] = HttpSandboxes(str(cfg["lease_runner_url"]), str(cfg["lease_runner_token_file"]),
                                              httpx.Client(timeout=30.0))
    return external


def require_secrets(config: Config, keys: VapidKeys) -> str:
    """Muninn's own keys are where config says: the push key and the ingest token."""
    missing = [] if keys.present() else [str(keys.path)]
    if not (config.secrets_dir / "ingest_token").is_file():
        missing.append(str(config.secrets_dir / "ingest_token"))
    if missing:
        raise MissingSecret(f"missing: {', '.join(missing)} (muninn init creates them; restore them if they were lost)")
    return "push key and ingest token present"


def smoke_checks(store, engine: Engine, backends: list, google, lease_runner, secrets=None) -> list:
    """What a deploy checks inside the running server, whose environment
    (PATH, keychain, network) is what counts. Read-only; no model is called."""

    from muninn.adapters.web.app import unbuilt

    def must(ok: bool, detail: str) -> str:
        if not ok:
            raise RuntimeError(detail)
        return detail

    checks = [
        *([("secrets", secrets)] if secrets else []),
        ("store", lambda: f"{len(store.tasks(status=None))} tasks"),
        ("open items", lambda: f"{len(engine.open_items.items())} open, {len(engine.review_queue())} to review"),
        ("web app", lambda: must(not unbuilt(), "built" if not unbuilt() else
                                 f"not built ({', '.join(unbuilt())} missing): npm ci && npm run build, then install again")),
    ]
    for b in backends:
        checks.append((f"{'local' if b.is_local else 'cloud'} model ({b.name})", b.check))
    if google.configured:
        checks.append(("google", lambda: must(google.status() == "ok", google.status())))
    if lease_runner is not None:
        checks.append(("session runner", lambda: f"{len(lease_runner.policy().get('attachable') or [])} attachable projects"))
    if engine.applications.sites:
        from muninn.adapters.apply.browser import installed

        def browser() -> str:
            if not installed():
                raise RuntimeError("no browser to fill them in: playwright install chromium")
            return "Greenhouse, browser installed"

        checks.append(("application forms", browser))
    return checks


def settings_view(config: Config, c: ComposerConfig, problems: Sequence[str] = (), off: Sequence[dict] = ()) -> dict:
    words = language(config.language)
    return {
        "quiet_hours": f"{c.quiet_start:%H:%M}–{c.quiet_end:%H:%M}",
        "interrupt_max_priority": c.interrupt_max_priority,
        "escalation_level": c.escalation_level,
        "daily_push_cap": c.daily_push_cap,
        "tier_quota": dict(c.tier_quota),
        "report_times": config["report_times"],
        "language": config.language,
        "allowed_logins": bool(config.raw.get("allowed_tailscale_logins")),
        **cloud_settings(config["llm"], words),
        "local_model": local_model_name(config),
        "problems": list(problems),
        "off": list(off),
    }


# the providers config may name (llm.providers.<name>.impl), one implementation each
IMPLS = {"claude": ClaudeCliProvider, "anthropic": AnthropicApiProvider, "openai": OpenAICompatProvider, "ollama": OllamaProvider}


class Providers:
    """The providers config names, made once: the backend for each provider and model, the cloud's and the local one."""

    def __init__(self, setup: LLMSetup, ctx: ProviderContext, meter: Callable[[LLMBackend], LLMBackend] = lambda b: b):
        """`meter`: what every backend made goes through (the tokens of each reply kept: what a spending limit counts)."""
        self.setup = setup
        self.models = setup.task_models() if setup.cloud else None
        self._providers = {name: IMPLS[spec.impl].from_spec(spec, ctx) for name, spec in setup.providers.items()}
        self._meter = meter

    @property
    def cloud(self) -> bool:
        return self.setup.cloud

    def backend(self, choice: ModelChoice, timeout: float = 120.0):
        return self._meter(self._providers[choice.provider].backend(choice.model, timeout))

    def in_use(self) -> set[ModelChoice]:
        """Every cloud provider and model a request may reach: each kind of work's, and the routers' own."""
        return in_use(self.setup) if self.setup.cloud else set()

    def local(self, model: str | None = None, timeout: float = 180.0) -> LLMBackend | None:
        """The local provider's model (its fast tier's unless named); None without a local provider."""
        spec = self.setup.local
        return None if spec is None else self._meter(self._providers[spec.name].backend(model or spec.models["fast"], timeout))


def in_use(setup: LLMSetup) -> set[ModelChoice]:
    models = setup.task_models()
    return ({c for c in (models.model(g.tasks[0]) for g in GROUPS) if c} | set(models.points().values())
            | {setup.choice("fast"), setup.choice("standard")})


REGISTRY = CONTRACTS / "llm-points.yaml"  # every decision point, declared


def decision_points() -> set[str]:
    return set((YAML(typ="safe").load(REGISTRY.read_text(encoding="utf-8")) or {}).get("points") or {})


def providers_of(cfg: Config, meter: Callable[[LLMBackend], LLMBackend] = lambda b: b) -> Providers | None:
    """The configured providers (None: no model at all); `meter`, what each backend goes through (meter_into: every
    reply's tokens kept). ValueError for a config it cannot use as written."""
    setup = llm_setup(cfg["llm"], IMPLS, decision_points())
    if setup is None:
        return None
    return Providers(setup, ProviderContext(cfg.data_dir, cfg.secrets_dir, frozenset(setup.env_keys()), words=language(cfg.language)),
                     meter)


def meter_into(store: SqliteStore, tz: str) -> Callable[[LLMBackend], LLMBackend]:
    """Every reply's tokens kept in the store, whatever asked the model (what the daily limit counts)."""
    clock = SystemClock(tz)
    return lambda b: Metered(b, lambda name, tokens_in, tokens_out: store.add_usage(clock.now(), name, tokens_in, tokens_out))


def cloud_of(cfg: Config, meter: Callable[[LLMBackend], LLMBackend] = lambda b: b) -> Providers | None:
    """The configured providers when one of them is a cloud (None: no cloud model at all)."""
    side = providers_of(cfg, meter)
    return side if side is not None and side.cloud else None


def local_model_name(cfg: Config) -> str | None:
    setup = llm_setup(cfg["llm"], IMPLS, decision_points())
    return setup.local.models["fast"] if setup is not None and setup.local is not None else None


def cloud_settings(llm: dict, words: Language) -> dict:
    """What the settings page shows of the cloud: routing, each kind of work's model, and what the user calls
    the cloud (the providers in use, in the order config lists them)."""
    setup = llm_setup(llm, IMPLS, decision_points())
    routing = "cloud" if llm.get("routing") == "cloud" else "privacy"  # as written: a mismatch is a problem, not a quiet fallback
    if setup is None or not setup.cloud:
        return {"routing": routing, "models": [], "cloud_label": None}
    names = {c.provider for c in in_use(setup)}
    labels = dict.fromkeys(setup.providers[n].options.get("label") or IMPLS[setup.providers[n].impl].label(words)
                           for n in setup.providers if n in names)
    return {"routing": routing,
            "models": described(setup.task_models().table(), words), "cloud_label": words.join(labels)}


def _print_items(items) -> None:
    if not items:
        print("(nothing new: already done for this period, or nothing to report)")
    for i in items:
        print(f"── {i.title}\n{i.body}\n")


def eval_candidate(cfg: Config, backend: str | None, local_model: str | None,
                   meter: Callable[[LLMBackend], LLMBackend] = lambda b: b) -> LLMBackend:
    """The model `muninn eval` scores: `backend` as provider:model (ollama:<model> for a local one), else the local
    model named or configured. ValueError, saying what to name, when there is none."""
    llm = cfg["llm"]
    timeout = float(llm.get("eval_timeout", 300))
    if backend:
        provider, _, model = backend.partition(":")
        if not model:
            raise ValueError(f"--backend {backend}: write it as provider:model")
        side = providers_of(cfg, meter)
        if side is not None and provider in side.setup.providers:
            return side.backend(ModelChoice(provider, model), timeout)
        if provider == "ollama":  # an Ollama model on this machine, set up or not
            return OllamaBackend(model, llm["ollama_url"], timeout=timeout, words=language(cfg.language))
        known = ["ollama", *(side.setup.providers if side else ())]
        raise ValueError(f"--backend {backend}: no provider {provider!r} in llm.providers; known: {', '.join(dict.fromkeys(known))}")
    side = providers_of(cfg, meter)
    if local_model or (side is not None and side.setup.local is not None):
        found = side.local(local_model, timeout) if side is not None and side.setup.local is not None else None
        return found or OllamaBackend(str(local_model), llm["ollama_url"], timeout=timeout, words=language(cfg.language))
    raise ValueError("no model to score: name one with --backend provider:model, or set up a local model")


def load_suite() -> dict[str, dict[str, Any]]:
    """The invented cases shipped with Muninn, by language and task (<task>.<language>.yaml): a language with no file
    for a task has no cases for it."""
    folder = resources.files("muninn") / "eval_suite"
    out: dict[str, dict[str, Any]] = {}
    for code in CODES:
        for t in eval_suite.TASKS:
            if (f := folder / f"{t}.{code}.yaml").is_file():
                out.setdefault(code, {})[t] = YAML(typ="safe").load(f.read_text(encoding="utf-8"))
    return out


def suite_systems(codes: Sequence[str], tools: str) -> dict[tuple[str, str], str]:
    """Each suite task's prompt in each language, as the decision points build them (`tools`: the routing tool list)."""
    out = {}
    for code in codes:
        words = language(code)
        out |= {("route", code): command_prompt.system(words, tools), ("facts", code): facts_prompt.system(words),
                ("work_check", code): WORK_SYSTEM}
    return out


def suite_model(cfg: Config, task: str, backend: str | None, meter: Callable[[LLMBackend], LLMBackend] = lambda b: b) -> LLMBackend:
    """The model the suite asks for a task: the one named, else the one the task uses here (the work check is the
    local model's when there is one)."""
    if backend:
        return eval_candidate(cfg, backend, None, meter)
    side = providers_of(cfg, meter)
    if side is None:
        raise ValueError("no model set up: name one with --backend provider:model")
    if (task == "work_check" or not side.cloud) and (local := side.local(timeout=300)) is not None:
        return local
    assert side.models is not None
    default = side.setup.choice("standard" if task in ("route", "facts") else "fast")
    return side.backend(side.models.model(task) or default, 300)


# the commands that ask models: refused while the model settings cannot work as written (App.problems)
NEEDS_MODELS = frozenset({"tick", "morning", "evening", "weekly", "prepare", "reflect", "reflection", "mail", "schedule", "eval"})


def access_problems(cfg: Config) -> list[str]:
    """Access settings that do not protect what they seem to."""
    words = language(cfg.language)
    if cfg.raw.get("allowed_tailscale_logins") and cfg["host"] not in ("127.0.0.1", "localhost", "::1"):
        return [words("main.logins_need_serve", host=cfg["host"])]
    return []


def backup(cfg: Config, into: Path, *, with_secrets: bool = False) -> Path:
    """A copy of this installation in a new folder under `into`: the database (taken whole while it is in use), the
    plans and the settings; the keys only when asked. Restoring is copying them back into the data directory."""
    import shutil
    import sqlite3

    stamp = f"muninn-backup-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    into.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:  # two in the same second each get their own folder
        dest = into / (stamp if n == 1 else f"{stamp}-{n}")
        try:
            dest.mkdir(mode=0o700)
            break
        except FileExistsError:
            n += 1
    if cfg.db_path.exists():
        src = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True)
        with sqlite3.connect(dest / cfg.db_path.name) as copy:
            src.backup(copy)
        src.close()
    if cfg.domains_dir.is_dir():
        shutil.copytree(cfg.domains_dir, dest / cfg.domains_dir.name)
    for name in ("config.yaml", "attribution.yaml"):
        if (cfg.data_dir / name).is_file():
            shutil.copy2(cfg.data_dir / name, dest / name)
    if with_secrets and cfg.secrets_dir.is_dir():
        shutil.copytree(cfg.secrets_dir, dest / "secrets")
    for p in [dest, *dest.rglob("*")]:
        p.chmod(0o700 if p.is_dir() else 0o600)
    return dest


def first_run_token(cfg: Config, devices: DeviceRegistry) -> str | None:
    """While no device is paired, the token for the setup page, said at start and kept in <data>/setup-url (readable
    only by its owner) for the install to open; None once a device is paired. The same token until then: saving the
    model settings restarts Muninn, and the page still open goes on to pair the phone with the token it has."""
    url_file = cfg.data_dir / "setup-url"
    if devices.any_paired():
        url_file.unlink(missing_ok=True)
        return None
    kept = url_file.read_text(encoding="utf-8").strip().partition("#setup=")[2] if url_file.is_file() else ""
    token = kept or secrets.token_urlsafe(18)
    url = f"http://127.0.0.1:{cfg.port}/#setup={token}"  # after # it is never sent to the server or kept in its log
    fd = os.open(url_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(url + "\n")
    log.warning("first run: set Muninn up at %s (also in %s)", url, url_file)
    return token


def form_filling_ready() -> bool:
    """Whether application forms can be filled in here: the autofill extra, and the browser it drives."""
    from muninn.adapters.apply import browser

    return browser.available() and browser.installed()


def restart_self() -> bool:
    """Starts Muninn again with new settings: under a service manager (the install sets MUNINN_SERVICE), by
    stopping it a moment after the answer goes, so the manager starts it again; otherwise False, the user does."""
    if not os.environ.get("MUNINN_SERVICE"):
        return False
    import signal

    threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    return True


def upgrade(args: argparse.Namespace) -> int:
    """`muninn upgrade`: wired here, the steps are upgrade.py's. In the old code it installs the release, then the
    new code (run as its own process, since the files under this one were replaced) restarts and checks."""
    from muninn.upgrade import Upgrader

    try:
        port = Config.load(args.data).port
    except BadSetting as err:
        print(err, file=sys.stderr)
        return 2
    source = installs.WheelFile(args.wheel) if args.wheel else installs.PyPI()
    up = Upgrader(version(), source, installs.installation(), installs.service(), installs.served_version(port), lambda line: print(line, flush=True))
    if args.finish:
        return up.finish(args.finish, args.to)
    if args.check:
        from muninn.upgrade import UpgradeError

        try:
            up.check()
        except UpgradeError as err:
            print(f"could not tell the latest release: {err}", file=sys.stderr)
            return 1
        return 0

    def handover(previous: str, target: str) -> int:
        new = Path(sys.prefix) / "bin" / "muninn"  # the same place, now the new release's
        data = ["--data", args.data] if args.data else []
        return subprocess.run([str(new), *data, "upgrade", "--finish", previous, "--to", target]).returncode

    return up.run(handover)


def print_config(cfg: Config) -> None:
    from ruamel.yaml import YAML

    y = YAML()
    y.default_flow_style = False
    y.dump(dict(sorted(cfg.effective().items())), sys.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="muninn")
    parser.add_argument("--data", help="data directory (default: $MUNINN_DATA or ./data)")
    parser.add_argument("--dry-run", action="store_true", help="print pushes instead of sending them")
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"muninn {version()}")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("tick", "morning", "evening", "weekly", "serve", "init", "status"):
        sub.add_parser(name)
    sub.add_parser("check", help="what in the settings cannot work as written, then the running checks (/api/smoke) here")
    p_prep = sub.add_parser("prepare", help="prepare application packets now")
    p_prep.add_argument("--target", help="one tracker target id (default: the most urgent)")
    p_prep.add_argument("--limit", type=int, default=1)
    p_pair = sub.add_parser("pair")
    p_pair.add_argument("--name", default="")
    p_reflect = sub.add_parser("reflect", help="nightly: read conversation text and digest sessions")
    p_reflect.add_argument("--budget", type=float, default=60, help="minutes (default 60)")
    p_refl = sub.add_parser("reflection", help="write this week's reflection now")
    p_refl.add_argument("--preview", action="store_true", help="store without a push, keep the weekly run")
    p_conn = sub.add_parser("connect", help="print a link to connect an account (google | microsoft)")
    p_conn.add_argument("provider", choices=["google", "microsoft"])
    p_sched = sub.add_parser("schedule", help="put today's priorities on the calendar")
    p_sched.add_argument("--force", action="store_true", help="re-plan even if done today")
    p_mail = sub.add_parser("mail", help="read new mail now")
    p_mail.add_argument("--limit", type=int, default=10)
    p_req = sub.add_parser("requests", help="what the user asked Muninn for that it cannot do yet")
    p_req.add_argument("--commit", nargs=2, metavar=("ID", "COMMIT"), help="the commit that implements request ID: "
                       "it is marked live once the running Muninn includes it")
    p_eval = sub.add_parser("eval", help="score a model on the recorded cases (default: the local one)")
    p_eval.add_argument("--backend", metavar="PROVIDER:MODEL",
                        help="the model to score: ollama:<model>, or a provider in llm.providers and its model (e.g. anthropic:claude-haiku-5-5)")
    p_eval.add_argument("--replay-days", type=int, default=0, help="first run the notes of the last N days through today's models")
    p_eval.add_argument("--task", help="only this task (route, facts, answer, why, window_classify, mail_triage, ...)")
    p_eval.add_argument("--limit", type=int, default=200)
    p_eval.add_argument("--coverage", action="store_true", help="per decision point (contracts/llm-points.yaml): cases kept and how many carry the user's word")
    p_eval.add_argument("--decisions", action="store_true",
                        help="per decision point (kind: decide): how often the recorded decisions were right by the user's own word, and their time")
    p_eval.add_argument("--model", help="local model to score (default: llm.local_model)")
    p_eval.add_argument("--prompt-check", nargs=2, metavar=("BEFORE", "AFTER"),
                        help="prompt snapshots (bin/prompt_snapshot.py): run the recorded cases with each, on the model each task uses, and list what changed")
    p_eval.add_argument("--workers", type=int, default=4, help="cases run at once (--prompt-check, --suite)")
    p_eval.add_argument("--suite", action="store_true",
                        help="the invented cases shipped with Muninn (route, facts, work_check) on the models the tasks use, or --backend; "
                             "in every language there are cases for, or --language")
    p_eval.add_argument("--language", choices=CODES, help="--suite: only the cases in this language (zh-TW, en)")
    p_tasks = sub.add_parser("tasks", help="what only the user can do (open ones; --all for every status)")
    p_tasks.add_argument("--json", action="store_true")
    p_tasks.add_argument("--project")
    p_tasks.add_argument("--all", action="store_true")
    p_hand = sub.add_parser("handoffs", help="facts to pass on to other projects (for the nightly delivery)")
    p_hand.add_argument("--since", type=int, default=0, help="only handoffs with a larger id (not for delivery: a held handoff can come due below it)")
    p_hand.add_argument("--json", action="store_true")
    p_hand.add_argument("--delivered", type=int, nargs="*", help="mark these ids delivered")
    p_ver = sub.add_parser("handoff-version", help="which Principal Handoff version each project is told its records in "
                           "(with PROJECT EXT VERSION: set it; PROJECT * is every project, those to come too)")
    p_ver.add_argument("setting", nargs="*", metavar="PROJECT EXT VERSION")
    p_dig = sub.add_parser("digests", help="one week's conversation digests (summaries, never conversation text)")
    p_dig.add_argument("--week", help="ISO week, e.g. 2026-W42 (default: the last complete week)")
    p_dig.add_argument("--json", action="store_true")
    p_forget = sub.add_parser("forget-repo", help="drop a repo name that is not a project")
    p_forget.add_argument("repo")
    p_dev = sub.add_parser("devices")
    p_dev.add_argument("--revoke", type=int)
    p_bak = sub.add_parser("backup", help="copy the database, plans and settings into a new folder under DIR")
    p_bak.add_argument("dir")
    p_bak.add_argument("--secrets", action="store_true", help="the keys too (the copy is then as sensitive as they are)")
    p_up = sub.add_parser("upgrade", help="install the latest stable release and restart on it (put back if it does not come up)")
    p_up.add_argument("--check", action="store_true", help="only say which version is installed and which is the latest")
    p_up.add_argument("--from", dest="wheel", help="a muninn-ai package file (path or address) instead of the latest on PyPI")
    p_up.add_argument("--finish", help=argparse.SUPPRESS)  # the previous version: the new code takes over from here
    p_up.add_argument("--to", help=argparse.SUPPRESS)
    p_cfg = sub.add_parser("config", help="print the settings in effect (config.yaml over the defaults); changes nothing")
    p_cfg.add_argument("--effective", action="store_true", help="the merged settings, with what is worked out from them")
    args = parser.parse_args(argv)
    if args.cmd == "config":  # before build(): reading the settings needs no keys and opens nothing
        try:
            print_config(Config.load(args.data))
        except BadSetting as err:
            print(err, file=sys.stderr)
            return 2
        return 0
    if args.cmd == "upgrade":  # before build(): the server migrates its own database once it runs the new version
        return upgrade(args)
    if args.cmd == "backup":  # before build() too: a copy is taken of what is there, nothing is migrated first
        print(backup(Config.load(args.data), Path(args.dir).expanduser(), with_secrets=args.secrets))
        return 0

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        app = build(Config.load(args.data), dry_run=args.dry_run)
    except (BadSetting, MissingSecret, StoreTooNew) as err:  # said, and nothing made or read past in its place
        log.error("%s", err)
        return 2
    e = app.engine

    if args.cmd == "check":
        for p in [*app.problems, *access_problems(app.config)]:
            print(f"  !!  {p}")
        failed = bool(app.problems or access_problems(app.config))
        for name, check in app.smoke:
            try:
                print(f"  ok  {name}: {check()}")
            except Exception as err:
                failed = True
                print(f"  !!  {name}: {err}")
        for o in app.off:  # not a failure: what this installation does without
            print(f"  --  {o['feature']}: {o['why']}")
        return 1 if failed else 0
    if app.problems and args.cmd in NEEDS_MODELS:  # it would run with a model other than the one written, or none
        for p in app.problems:
            log.error("%s", p)
        return 2
    if app.problems and args.cmd == "serve":  # still served: the settings page says what to put right
        for p in app.problems:
            log.error("%s", p)

    if args.cmd == "init":
        created = app.keys.ensure()
        app.config.ingest_token(create=True)
        print(f"vapid key: {'created' if created else 'exists'}; ingest token: {app.config.secrets_dir / 'ingest_token'}")
        return 0
    if args.cmd == "tick":
        comp = e.tick()
        print(f"tick: {len(comp.items)} new alert(s), {len(comp.pushes)} push(es)")
        return 0
    if args.cmd == "morning":
        _print_items(e.morning())
        return 0
    if args.cmd == "evening":
        _print_items(e.evening())
        return 0
    if args.cmd == "weekly":
        _print_items(e.weekly())
        return 0
    if args.cmd == "prepare":
        made = e.prepare_packets(limit=args.limit, only=args.target)
        if not made:
            print("nothing prepared (no candidate, posting unreadable, no documents configured, or no model available)")
        for p in made:
            print(f"prepared #{p.id}: {p.title} (by {p.prepared_by})")
        return 0
    if args.cmd == "reflect":
        new, done = e.reflect_digest(budget_s=args.budget * 60, reserve_s=float(app.config["llm"].get("reflect_timeout", 180)))
        print(f"reflect: {new} new turn(s) read, {done} session(s) digested")
        return 0
    if args.cmd == "reflection":
        _print_items(e.reflection(preview=args.preview))
        return 0
    if args.cmd == "connect":
        print(app.connections[args.provider].authorization_url())
        return 0
    if args.cmd == "schedule":
        for a in e.schedule_today(force=args.force):
            print(a.text)
        return 0
    if args.cmd == "mail":
        print(f"{e.triage_mail(limit=args.limit)} message(s) read")
        return 0
    if args.cmd == "eval" and (args.coverage or args.decisions):
        points = (YAML(typ="safe").load(REGISTRY.read_text(encoding="utf-8")) or {}).get("points") or {}
        if args.decisions:
            for row in decisions(points, e.store.eval_cases(limit=100_000)):
                right = f"{row['right']}/{row['settled']} right" if row["right"] is not None else "no truth yet"
                ms = f"{row['mean_ms'] / 1000:.1f}s" if row["mean_ms"] is not None else "-"
                print(f"{row['point']:16} {row['cases']:5} cases  {right:14} {ms:>7} each  by {', '.join(row['by']) or '-'}")
            return 0
        for row in coverage(points, e.store.eval_cases(limit=100_000)):
            print(f"{row['point']:16} {row['kind']:8} {row['scorer']:16} {row['cases']:5} cases  {row['with_feedback']:4} with the user's word")
        return 0
    if args.cmd == "eval" and args.suite:
        try:
            chosen = {t: suite_model(app.config, t, args.backend, meter_into(e.store, app.config.tz)) for t in eval_suite.TASKS}
        except ValueError as err:
            print(err, file=sys.stderr)
            return 2
        suite = load_suite()
        if args.language is not None and args.language not in suite:
            print(f"no suite cases in {args.language} (there are: {', '.join(suite) or 'none'})", file=sys.stderr)
            return 2
        codes = [args.language] if args.language else list(suite)
        systems = suite_systems(codes, e.commands().tools.tool_list())

        def ask(task: str, system: str, prompt: str, json_mode: bool) -> str | None:
            b = chosen[task]
            return b.complete(system, prompt if b.is_local else redact(prompt)[0], json_mode=json_mode)

        suite_cases = [c for code in codes for c in eval_suite.cases(suite[code], code)]
        progress = (lambda n, total: print(f"\r{n}/{total}", end="", file=sys.stderr, flush=True))
        reports = eval_suite.run(suite_cases, systems, {t: b.name for t, b in chosen.items()}, ask,
                                 ReplyCheck(schemas(REGISTRY.parent)), workers=args.workers, on_case=progress)
        print(file=sys.stderr)
        path = app.config.data_dir / "evals" / f"{e.clock.now():%Y%m%d-%H%M%S}-suite.json"
        path.parent.mkdir(exist_ok=True)
        tasks: dict[str, dict[str, Any]] = {}
        for (t, code), r in reports.items():
            tasks.setdefault(t, {})[code] = {
                "model": r.model, "mean": r.mean, "cases": len(r.scores), "off_shape": r.off_shape,
                **({"wrong_language": r.wrong_language} if t == "route" else {}),
                "mean_ms": sum(r.ms) / len(r.ms) if r.ms else None, "misses": r.misses}
        path.write_text(json.dumps({"at": e.clock.now().isoformat(), "tasks": tasks}, ensure_ascii=False, indent=1, default=str))
        for (t, code), r in reports.items():
            took = f"{sum(r.ms) / len(r.ms) / 1000:.1f}s each" if r.ms else ""
            wrong = f"  {r.wrong_language:2} wrong language" if t == "route" else ""
            print(f"{t:12} {code:6} {r.model:32} {r.mean or 0:.2f}  {len(r.scores):3} cases  {r.off_shape:2} off shape{wrong}  {took}")
        print(f"misses: {path}")
        return 0
    if args.cmd == "eval" and args.prompt_check:
        cloud = cloud_of(app.config, meter_into(e.store, app.config.tz))
        if cloud is None:
            print("no cloud provider configured: the prompts are checked on the models the tasks use", file=sys.stderr)
            return 2
        lock = threading.Lock()

        def complete(task: str, system: str, prompt: str, json_mode: bool) -> str | None:
            with lock:
                backend = cloud.backend(cloud.models.model(task) or cloud.setup.choice("fast"))
            try:
                return backend.complete(system, redact(prompt)[0], json_mode=json_mode)  # as the router sends it
            except Exception as err:  # a reply that failed is a decision of its own: none
                return f"<error: {err}>"

        before, after = (json.loads(Path(p).read_text(encoding="utf-8")) for p in args.prompt_check)
        # the cloud reads the cases routing lets it read: under privacy, only those it was allowed when recorded
        cloud_first = app.config["llm"].get("routing") == "cloud"
        cases = [c for c in e.store.eval_cases(args.task, args.limit) if cloud_first or c["cloud_ok"]]
        progress = (lambda n, total: print(f"\r{n}/{total}", end="", file=sys.stderr, flush=True))
        results = prompt_check.run(cases, before, after, complete, workers=args.workers, on_case=progress)
        print(file=sys.stderr)
        path = app.config.data_dir / "evals" / f"{e.clock.now():%Y%m%d-%H%M%S}-prompt-check-{args.task or 'all'}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"at": e.clock.now().isoformat(), "summary": prompt_check.summary(results),
                                    "cases": [{**asdict(r), "verdict": r.verdict} for r in results if r.changed]},
                                   ensure_ascii=False, indent=1, default=str))
        for task, row in sorted(prompt_check.summary(results).items()):
            print(f"{task:16} {row['cases']:4} cases  {row['same']:4} same  {row['changed']:3} changed  {row['noisy']:3} noisy")
        print(f"what changed: {path}")
        return 0
    if args.cmd == "eval":
        if args.replay_days:
            print(f"replayed {e.replay_notes(e.clock.now() - timedelta(days=args.replay_days))} note(s)", file=sys.stderr)
        try:
            candidate = eval_candidate(app.config, args.backend, args.model, meter_into(e.store, app.config.tz))
        except ValueError as err:
            print(err, file=sys.stderr)
            return 2
        progress = (lambda n, total: print(f"\r{n}/{total}", end="", file=sys.stderr, flush=True))
        reports = Evaluator(e.store, candidate, ModelJudge(e.understanding.router), progress).run(args.task, args.limit)
        print(file=sys.stderr)
        # the judge is the understanding model: of the candidate's own family, it may favour it; read the scores so
        judge = sorted({b.name for b in e.understanding.router.backends()})
        out = {"model": candidate.name, "judge": judge, "at": e.clock.now().isoformat(),
               "tasks": {t: {"mean": r.mean, "scored": r.scored, "skipped": r.skipped, "mean_ms": r.mean_ms,
                             "recorded_mean_ms": r.ref_mean_ms, "misses": r.misses} for t, r in reports.items()}}
        path = app.config.data_dir / "evals" / f"{e.clock.now():%Y%m%d-%H%M}-{candidate.name.replace(':', '_').replace('/', '_')}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str))
        for t, r in sorted(reports.items()):
            mean = f"{r.mean:.2f}" if r.mean is not None else "-"
            took = f"  {r.mean_ms / 1000:.1f}s each" if r.mean_ms is not None else ""
            was = f" (recorded {r.ref_mean_ms / 1000:.1f}s)" if r.ref_mean_ms is not None else ""
            print(f"{t:7} {mean}  ({r.scored} scored, {r.skipped} without a reference){took}{was}")
        print(f"misses: {path}")
        return 0
    if args.cmd == "tasks":
        items = e.store.tasks(status=None if args.all else "open", project=args.project)
        if args.json:  # contracts/tasks.v1.schema.json
            print(json.dumps([{"v": CONTRACT_V, **t} for t in items], ensure_ascii=False, indent=1))
        else:
            for t in items:
                where = e.words.join(sorted({o["project"] for o in t["origins"]})) or "muninn"
                print(f"T-{t['n']:<4} {t['status']:<8} {t['due'] or '':<10} [{where}] {t['title']}")
        return 0
    if args.cmd == "handoffs":
        if args.delivered:
            for hid in args.delivered:
                e.set_handoff_status(hid, "delivered")
            return 0
        items = e.handoffs(since=args.since)
        if args.json:  # contracts/handoffs.v1.schema.json
            print(json.dumps([{"v": CONTRACT_V, **h} for h in items], ensure_ascii=False, indent=1))
        else:
            for h in items:
                item = e.words("main.handoff_item", effect=h["effect"], item=h["item"]) if h.get("item") else ""
                print(f"#{h['id']} [{h['project']}] {h['fact']}{item} {h.get('why') or ''}")
        return 0
    if args.cmd == "handoff-version":
        if args.setting:
            if len(args.setting) != 3:
                parser.error("handoff-version takes PROJECT EXT VERSION, e.g. agent-hub acts 0.5")
            try:
                e.handoff_versions.set(*args.setting)
            except ValueError as err:
                parser.error(str(err))
        print(json.dumps(e.handoff_versions.table(), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "digests":
        week = args.week or iso_week(e.clock.now() - timedelta(days=7))
        try:
            out = e.week_digests(week)
        except ValueError as err:
            parser.error(str(err))
        if args.json:  # contracts/digests.v1.schema.json
            print(json.dumps({"v": CONTRACT_V, **out}, ensure_ascii=False, indent=1))
        else:
            for d in out["sessions"]:
                print(f"{d['date']} [{d['project'] or '-'}] {d['summary']}")
            print(f"{out['week']}: {len(out['sessions'])} digest(s), {out['withheld']} withheld, "
                  f"{out['undigested_sessions']} session(s) not digested yet")
        return 0
    if args.cmd == "requests":
        if args.commit:
            rid, commit = args.commit
            if not rid.isdigit() or not e.request_commit(int(rid), commit):
                print(f"no open request {rid}", file=sys.stderr)
                return 1
            print(f"request {rid}: live once {commit[:12]} runs")
            return 0
        for r in e.feature_requests():
            print(f"{r.get('id') or '-':>3}  {r['ts'][:16]}  {r['text']}" + (f"  (commit {r['commit'][:12]})" if r.get("commit") else ""))
        return 0
    if args.cmd == "forget-repo":
        print(f"{e.forget_repo(args.repo)} observation(s) no longer tied to '{args.repo}'")
        return 0
    if args.cmd == "status":
        print(json.dumps(e.today_view(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "pair":
        code = app.devices.new_pairing_code()
        print(f"pairing code (valid 10 minutes, single use): {code}")
        return 0
    if args.cmd == "devices":
        if args.revoke is not None:
            print("revoked" if app.devices.revoke(args.revoke) else "no such device")
            return 0
        for d in app.devices.devices():
            print(f"{d['id']:>3}  {d['name']:<20} created {d['created'][:16]}  last seen {(d['last_seen'] or '-')[:16]}  {'REVOKED' if d['revoked'] else ''}")
        return 0
    if args.cmd == "serve":
        import uvicorn

        from muninn.adapters.web.app import create_app
        from muninn.core.day_view import DayView

        try:
            require_secrets(app.config, app.keys)  # missing: refuse to start rather than make new ones
        except MissingSecret as err:
            log.error("not starting: %s", err)
            return 2
        web = create_app(
            e,
            app.keys,
            app.config.ingest_token(),
            app.devices,
            allowed_logins=app.config.raw.get("allowed_tailscale_logins") or (),
            settings_view=settings_view(app.config, e.composer.config, app.problems, app.off),
            document_files=app.documents.files,
            connections=app.connections,
            public_url=app.config.public_url,
            messages=app.messages,
            lease_runner=app.lease_runner,
            permissions=app.permissions,
            smoke_checks=app.smoke,
            day_view=DayView(e.store, e.clock, e.tz, e.plans.load, e.metrics, e.stays_between, e.calendar, words=e.words),
            uploads=FileUploads(app.config.data_dir / "uploads", words=e.words),
            location_apps=LOCATION_APPS,
            setup=Setup(app.config, IMPLS, restart_self, first_run_token(app.config, app.devices), home=Path.home()),
            tailscale=Tailscale(),
            port=app.config.port,
            form_filling=FormFilling(installs.installation(), version(), form_filling_ready, restart_self),
        )
        try:
            jobs = scheduler.plan(app.config["jobs"])
        except ValueError as err:
            log.error("not starting: %s", err)
            return 2
        if jobs:  # this installation's own schedule; unset, another scheduler runs the commands
            data = str(app.config.data_dir)
            scheduler.Schedule(jobs, app.config.tz, e.clock.now,
                               lambda job, a: subprocess.run([sys.executable, "-m", "muninn", "--data", data, *a]).returncode,
                               e.store.get_state, e.store.set_state).start()
        uvicorn.run(web, host=app.config["host"], port=app.config.port, log_level="info", proxy_headers=False)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
