"""A whole Muninn behind its web app, with every optional part switched on, for tests that look at
what the pages see: the newcomer (no domain, nothing yet) and a full world with something on every
page. Models, clock and data are the unit tests' fakes, so every run is the same.

`full()` puts something everywhere through the same paths the app itself uses (the API, the
engine, the actors), one function per part, so a part can also be seeded on its own."""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from conftest import CANARY, at, make_harness
from muninn.adapters.channels.webpush import VapidKeys
from muninn.adapters.project_records import FileProjectRecords
from muninn.adapters.uploads import FileUploads
from muninn.adapters.web.app import create_app
from muninn.main import IMPLS, LOCATION_APPS
from muninn.setup_flow import Setup
from muninn.adapters.web.devices import DeviceRegistry
from muninn.config import Config
from muninn.core.actions import DecidedBy, PreparePacket
from muninn.core.composer import ComposerConfig
from muninn.core.day_view import DayView
from muninn.core.decide import Verdict
from muninn.core.lease import ChannelLease, PermissionDesk
from muninn.core.mail import MailTriager
from muninn.core.models import CalendarEvent, InboxItem, MailMessage, Observation, Posting, Sensitivity
from muninn.core.prepare import PacketPreparer
from muninn.main import settings_view
from test_lease import FakeRunner

NOW = at(2026, 10, 22, 10)


class Calendar:
    """The user's calendar as the timeline reads it: their `mine` events and Muninn's `blocks`."""

    def __init__(self):
        self.mine: list[CalendarEvent] = []
        self.blocks: list[CalendarEvent] = []

    def available(self):
        return True

    def busy(self, start, end):
        return [(e.start, e.end) for e in self.events(start, end)]

    def create(self, title, start, end, description=""):
        self.blocks.append(CalendarEvent(f"b{len(self.blocks) + 1}", start, end, title))
        return self.blocks[-1].id

    def delete(self, event_id):
        self.blocks = [e for e in self.blocks if e.id != event_id]

    def events(self, start, end):
        return [e for e in self.mine if e.start < end and e.end > start]

    def own_events(self, start, end):
        return [e for e in self.blocks if e.start < end and e.end > start]


class Connection:
    def __init__(self, name, configured=True, authorized=False, lapsed=False):
        self.name, self.configured, self.authorized, self.lapsed = name, configured, authorized, lapsed

    def status(self):
        return "relink" if self.lapsed else "ok" if self.authorized else "not linked"

    def authorization_url(self):
        return "https://accounts.example/authorize?state=x"


class Mailbox:
    def __init__(self, name, kind, messages):
        self.name, self.kind, self.messages, self.drafts = name, kind, messages, []

    def available(self):
        return True

    def fetch(self, cursor, now):
        new = [m for m in self.messages if cursor is None or m.ts.timestamp() > cursor]
        return new, (max(m.ts.timestamp() for m in self.messages) if self.messages else cursor)

    def cursor_of(self, m):
        return m.ts.timestamp()

    def create_draft(self, reply_to, body):
        self.drafts.append((reply_to.thread_id, body))
        return "d1"

    def get(self, message_id):
        return next((m for m in self.messages if m.id == message_id), None)


class Fetcher:
    def fetch(self, url):
        return Posting(url, "Staff Engineer", "Taipei", "Requirements: Go, TypeScript, 5 years.")


class Docs:
    def documents(self):
        return [("resume-en", "Go and TypeScript, 8 years.")]


class FitDecider:
    """Every fit check finds a fit."""

    def decide(self, q):
        return Verdict("fit", None, "cloud", "符合")


class ListedSetup(Setup):
    """Setup whose service lists two models without being asked over the network."""

    def models(self, choice):
        super()._spec(choice)
        return ["model-small", "model-large"]


class World:
    def __init__(self, tmp: Path, *, newcomer: bool = False):
        self.tmp = tmp
        self.h = make_harness(NOW, plans=[] if newcomer else None, confirm_all=False)
        e = self.e = self.h.engine
        keys = VapidKeys(tmp / "secrets" / "vapid.pem")
        keys.ensure()
        self.devices = DeviceRegistry(tmp / "dev.db")
        self.runner = FakeRunner()
        e.leases = ChannelLease(e.store, self.h.clock, self.runner, self.h.channel, lambda now: False,
                                lambda tid: e.conversation().thread(tid), lambda tid, change: e.conversation().update_thread(tid, change), words=e.words)
        self.desk = e.permissions = PermissionDesk(e.store, self.h.clock, self.h.channel, lambda now: False, e.leases, words=e.words)
        self.calendar = Calendar()
        day = DayView(e.store, self.h.clock, e.tz, e.plans.load, e.metrics, e.stays_between, self.calendar, words=e.words)
        (tmp / "resume-en.md").write_text("Go and TypeScript, 8 years.\n", encoding="utf-8")
        self.connections = {"google": Connection("google", authorized=True), "microsoft": Connection("microsoft")}
        settings = settings_view(Config.load(tmp), ComposerConfig(tz=e.tz))
        app = create_app(e, keys, "tok", self.devices, settings_view=settings,
                         document_files=lambda: {"resume-en": tmp / "resume-en.md"}, connections=self.connections,
                         messages=e.messages, lease_runner=self.runner, permissions=self.desk, day_view=day,
                         uploads=FileUploads(tmp / "uploads", words=e.words), location_apps=LOCATION_APPS,
                         setup=ListedSetup(Config.load(tmp), IMPLS, restart=lambda: False), public_url="https://muninn.example.ts.net")
        self.client = TestClient(app, base_url="https://testserver")
        r = self.client.post("/api/pair", json={"code": self.devices.new_pairing_code(), "name": "test phone"})
        assert r.status_code == 200, r.text

    # ── the parts, each put there the way the app does it ────────────────────

    def activity(self) -> None:
        """Time in an editor this morning, attributed to jobs, and a commit in a repo nobody named (a question)."""
        t = NOW - timedelta(hours=2)
        self.e.ingest([Observation("windows", t, "window", "w1", {"duration_s": 3600, "title": "resume.md"}, "jobs", None,
                                   Sensitivity.PERSONAL),
                       Observation("git", t, "commit", "g1", {"repo": "side-thing"}, sensitivity=Sensitivity.PERSONAL)],
                      evaluate=False)

    def calendar_events(self) -> None:
        self.calendar.mine.append(CalendarEvent("e1", NOW + timedelta(hours=3), NOW + timedelta(hours=4), "Dentist"))
        self.calendar.blocks.append(CalendarEvent("b1", NOW + timedelta(hours=1), NOW + timedelta(hours=2), "Muninn · Applications"))
        self.e.calendar = self.calendar

    def place(self) -> None:
        """Where the user has been (OwnTracks posts), and the place named."""
        auth = {"authorization": "Basic " + base64.b64encode(b"kn:tok").decode()}
        for hours in (3, 1):
            body = {"_type": "location", "lat": 25.03, "lon": 121.56, "tst": int((NOW - timedelta(hours=hours)).timestamp()), "tid": "kn"}
            assert self.client.post("/api/ingest/owntracks", json=body, headers=auth).status_code == 200
        assert self.client.post("/api/places", json={"name": "家", "lat": 25.03, "lon": 121.56}).status_code == 200

    def packet(self) -> int:
        """A packet ready for a job (the fit check judged it a fit)."""
        fit = json.dumps({"verdict": "fit", "why": "符合"}, ensure_ascii=False)
        packet = json.dumps({"summary": "摘要", "match": ["Go"], "gaps": [], "resume": "resume-en", "resume_focus": "Go",
                             "answers": [{"q": "Why?", "a": "Because."}], "ask_first": []}, ensure_ascii=False)
        llms = (self.h.cloud, self.h.local)
        before = [llm.reply for llm in llms]
        for llm in llms:
            llm.reply = packet
        self.e.preparer = PacketPreparer(self.e.router, Fetcher(), Docs(), decider=FitDecider(), cloud_drafts=True)
        [p] = self.e.prepare_packets(limit=1)
        for llm, reply in zip(llms, before):
            llm.reply = reply
        return p.id

    def mail(self) -> None:
        m = MailMessage("m1", "t1", "personal", "personal", "Acme Talent <jobs@acme.example>", "me@example.com",
                        "Thanks for applying", NOW - timedelta(hours=1), "We received your application.", "<m1@acme>")
        self.e.mailboxes = [Mailbox("personal", "personal", [m])]
        self.e.triager = MailTriager(self.e.router, cloud_mail=True)
        reply = json.dumps({"category": "needs_reply", "company": "Acme", "summary": "問什麼時候方便面談", "action": "回覆時間",
                            "needs_reply": True, "draft": "Thanks! Tuesday works."}, ensure_ascii=False)
        for llm in (self.h.cloud, self.h.local):
            llm.reply = reply
        self.e.triage_mail()

    def ideas(self) -> None:
        self.e.ideas.add("做一個澆花提醒")

    def tasks(self) -> None:
        self.e.store.add_task("muninn:1", {"title": "打給牙醫", "due": "2026-10-23"}, NOW)
        self.e.store.add_task_origin("muninn:1", "garden", "t1", "打給牙醫問植牙")

    def inbox(self) -> int:
        item = self.e.store.add_inbox(InboxItem(None, NOW, "jobs", "reflection", "這週", "這週你在想投遞。",
                                                {"aggregate": {"sessions": 3, "themes": [["投遞", 2]]}}))
        return item.id

    def report(self) -> None:
        self.e.store.add_inbox(InboxItem(None, NOW - timedelta(hours=1), "muninn", "brief", "早安", "今天有兩件事。", {}))

    def attribution(self) -> None:
        """Muninn asked where the unknown repo's time goes, and the user answered."""
        self.e.tick()
        [c] = [c for c in self.e.pending_confirmations() if c.kind == "attribution"]
        assert self.client.post(f"/api/confirmations/{c.id}", json={"answer": c.options[0]}).status_code == 200

    def question(self) -> int:
        """Something Muninn asks before doing it."""
        ran = self.e.gate.run(PreparePacket("jobs", "acme", "Acme Staff Engineer", "履歷看不出管理經驗"), DecidedBy.INFERRED_UNSURE)
        return ran.confirm_id

    def lapsed_link(self) -> None:
        """A linked account whose sign-in lapsed: it has to be linked again."""
        self.connections["google"].lapsed = True
        self.e.connections = list(self.connections.values())

    def project_todos(self) -> None:
        """What the projects keep for the user to do (the external side's todo.md)."""
        todo = self.tmp / "todo.md"
        todo.write_text("<!-- contract: todo-md v1 -->\n# 跨專案待辦\n## 只有我能做\n### garden\n- [ ] 買肥料\n", encoding="utf-8")
        self.e.records = FileProjectRecords(str(todo), {"只有我能做": "me"}, str(self.tmp / "{name}" / "STATE.md"), None)

    def requests(self) -> None:
        self.e.add_feature_request("想要深色模式以外的主題")

    def project_question(self) -> str:
        self.e.messages.receive([{"id": "q1", "project": "garden", "type": "question", "text": "紅玫瑰還是白玫瑰?"}])
        return "q1"

    def lease(self) -> dict[str, Any]:
        got = self.e.leases.attach(None, "garden")
        self.desk.receive({"v": 1, "id": f"{got['lease_id']}:w", "lease": got["lease_id"], "project": "garden", "kind": "open_web",
                           "input_summary": "https://arxiv.org/abs/1"})
        self.desk.receive({"v": 1, "id": f"{got['lease_id']}:q", "lease": got["lease_id"], "project": "garden",
                           "kind": "ask_user", "input_summary": "A or B", "risk": "question",
                           "question": {"questions": [{"question": "A or B?", "options": [{"label": "A"}, {"label": "B"}]}]}})
        return got

    def talk(self) -> str:
        """A conversation with Muninn that offered a next step."""
        for llm in (self.h.cloud, self.h.local):
            llm.reply = json.dumps({"actions": [], "reply": "好,記下了。", "topic": {"title": "今天的投遞", "state": "open", "now": "等你選哪家先投"}, "question_for_him": "等你選哪家先投",
                                    "suggest": [{"label": "記成想法", "tool": "capture_idea", "args": {"text": "試試看水耕"}}]}, ensure_ascii=False)
        r = self.client.post("/api/command", json={"text": "今天先做投遞"})
        assert r.status_code == 200, r.text
        tid = r.json()["thread"]
        t = self.e.conversation().thread(tid)  # a deep talk's handover joined it: its words stay with that talk's project
        t["turns"].append({"role": "muninn", "text": "跟 garden 談完了。", "ts": t["ts"], "local_only": True, "via": "lease:L-world"})
        self.e.conversation().save_thread(tid, t)
        return tid

    def agreed(self) -> None:
        """The jobs plan agreed to (its goals count in the week); the other domains still wait in review."""
        from muninn.core.review import review_id

        p = next(p for p in self.e.plans.load() if p.id == "jobs")
        self.e.store.set_state("plan_review", {review_id(p.id, kind, x.id): "confirmed"
                                               for kind, items in (("goal", p.goals), ("deadline", p.deadlines)) for x in items})

    def full(self) -> "World":
        self.agreed()
        self.activity()
        self.calendar_events()
        self.place()
        self.packet()
        self.mail()
        self.ideas()
        self.tasks()
        self.inbox()
        self.report()
        self.attribution()
        self.question()
        self.requests()
        self.lapsed_link()
        self.project_todos()
        self.project_question()
        self.talk()
        self.lease()
        self.e.tick()
        assert CANARY  # the harness's private notes exist; the pages are free to show what the user wrote
        return self
