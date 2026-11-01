"""What the app shows, read from the actors' records (a read model: nothing here acts, except
that an alert whose goal is gone is marked read). The home page (what is ready, what Muninn did
today, what comes next, how the week stands), the alerts under it, a prepared item, and the day's
progress per domain."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Protocol, Sequence

from .actions import reversal
from .metrics import Metric
from .language import Language
from .models import ALL_STATUSES, ActionRecord, Confirmation, InboxItem, status_labels
from .plan_base import PlanBase
from .planner import build_agenda
from .ports import ActionLog, Calendar, Clock, CompletionStore, InboxStore, ObservationStore, PreparedStore
from .progress import INCLUSIVE, deadline_statuses
from .reports import agenda_data, goal_data
from .review import review_id
from .timewin import ActiveHours, local, local_date, period_window
from .language import Language

log = logging.getLogger(__name__)


class ViewStorage(ObservationStore, CompletionStore, PreparedStore, ActionLog, InboxStore, Protocol):
    """Observations (mail, progress), deadlines done, prepared items, what Muninn did, the inbox."""


class Views:
    def __init__(self, store: ViewStorage, clock: Clock, tz: str, hours: ActiveHours, metrics: Mapping[str, Metric],
                 base: PlanBase, *, calendar: Callable[[], Calendar | None], decisions: Callable[[], Mapping[str, str]],
                 pending: Callable[[], Sequence[Confirmation]], todos: Callable[[], dict[str, Any]],
                 relink: Callable[[], list[str]], day_stays: Callable[[], Sequence[Any]],
                 open_tasks: Callable[[], Sequence[dict[str, Any]]], ideas: Callable[[], Sequence[dict[str, Any]]],
                 talk: Callable[[str, str], dict[str, Any] | None], label: Callable[[str], str] = lambda name: name,
                 words: Language):
        """`base`: the plans and domains; `decisions`: the plan review's; `pending`: Muninn's open
        questions; `todos`: the projects' to-dos, owner → project → items; `relink`: connections
        whose authorization lapsed; `day_stays`: where the user was today; `open_tasks`: what only
        the user can do; `ideas`: the ideas kept, oldest first; `talk(kind, ref)`: the latest discussion about an item;
        `label(backend)`: what the user calls the model that prepared something."""
        self.words = words  # the language Muninn speaks to the user in
        self._label = label
        self._store = store
        self._clock = clock
        self._tz = tz
        self._hours = hours
        self._metrics = metrics
        self._base = base
        self._calendar = calendar
        self._decisions = decisions
        self._pending = pending
        self._todos = todos
        self._relink = relink
        self._day_stays = day_stays
        self._open_tasks = open_tasks
        self._ideas = ideas
        self._talk = talk

    def home(self) -> dict[str, Any]:
        """The butler's front page: what is ready for the user, what Muninn did
        today, what comes next, and how the week stands."""
        now = self._clock.now()
        plans = self._base.plans()
        day_start, _ = period_window("day", now, self._tz)
        agenda = build_agenda(plans, now, self._store, self._metrics, self._store.completed(), self._hours, words=self.words)
        top = []
        for a in agenda.domains:
            for p in a.today:
                top.append({"text": p.text, "why": p.why, "kind": p.kind, "target": p.target, "domain": a.plan.id,
                            "domain_title": a.plan.title, "domain_priority": a.plan.priority, "priority": p.priority})
        top.sort(key=lambda x: (x["domain_priority"], x["priority"]))
        ready = [
            {"id": p.id, "kind": p.kind, "domain": p.domain, "target": p.target, "title": p.title,
             "summary": p.data.get("packet", {}).get("summary") or p.data.get("summary"), "from": p.data.get("from"),
             "prepared_by": p.prepared_by, "prepared_by_label": self._label(p.prepared_by), "ts": p.ts.isoformat()}
            for p in self._store.prepared(status="ready")
        ]
        def still_stands(a: ActionRecord) -> bool:
            if a.undone:
                return False
            if a.undo.get("kind") == "dismiss_prepared":  # a packet replaced or set aside since
                p = self._store.get_prepared(int(a.undo["id"]))
                return p is not None and p.status != "dismissed"
            return True

        done = [
            {"id": a.id, "ts": a.ts.isoformat(), "domain": a.domain, "text": a.text, "undoable": reversal(a.undo) is not None}
            for a in self._store.actions(day_start) if still_stands(a)
        ]
        week = []
        for a in agenda.domains:
            if a.plan.observe_only or a.plan.priority > 3:
                continue
            for g in a.goals:
                if g.goal.period == "week":
                    d = goal_data(g, self.words)
                    week.append({"domain": a.plan.id, "title": g.goal.title, "value": d["value"], "state": d["state"]})
        reports = [
            {"id": i.id, "ts": i.ts.isoformat(), "kind": i.kind, "title": i.title, "body": i.body, "data": i.data}
            for i in self._store.inbox(limit=20) if i.kind in ("brief", "summary", "weekly") and i.ts >= day_start
        ]
        reflection = next(
            ({"id": i.id, "ts": i.ts.isoformat(), "title": i.title}
             for i in self._store.inbox(limit=50) if i.kind == "reflection" and now - i.ts < timedelta(days=8)),
            None,
        )
        todos = self._todos()
        mail = [
            {"ts": o.ts.isoformat(), "from": o.payload.get("from"), "subject": o.payload.get("subject"),
             "category": o.payload.get("category"), "summary": o.payload.get("summary"), "action": o.payload.get("action"),
             "thread": o.payload.get("thread"), "account": o.payload.get("account"), "id": o.payload.get("id")}
            for o in reversed(self._store.observations(now - timedelta(hours=36), now + INCLUSIVE, kinds=("mail",)))
            if o.payload.get("category") not in ("promo",)
        ][:12]
        schedule = []
        calendar = self._calendar()
        if calendar is not None:
            try:
                if calendar.available():
                    end = datetime.combine(local_date(now, self._tz), self._hours.end, local(now, self._tz).tzinfo)
                    schedule = [{"id": e.id, "title": e.title, "start": e.start.isoformat(), "end": e.end.isoformat()}
                                for e in calendar.own_events(day_start, end)]
            except Exception as e:
                log.warning("calendar read failed: %s", e)
        today_places = [
            {"start": st.start.isoformat(), "end": st.end.isoformat(), "hours": round(st.hours, 1),
             "place": st.place.name if st.place else None, "domain": st.place.domain if st.place else None,
             "lat": round(st.lat, 5), "lon": round(st.lon, 5)}
            for st in self._day_stays()
        ]
        return {
            "places": today_places,
            "relink": self._relink(),
            "mail": mail,
            "schedule": schedule,
            "now": now.isoformat(),
            "reflection": reflection,
            "my_project_todos": [{"project": p, "items": items} for p, items in todos.get("me", {}).items()],
            "ready": ready,
            "done": done,
            "next": top[:5],
            "week": week,
            "tasks": [{"key": t["key"], "n": t["n"], "title": t["title"], "due": t["due"], "context": t["context"],
                       "projects": sorted({o["project"] for o in t["origins"]}),
                       "mail": next((x[5:] for x in t["links"] if x.startswith("mail:")), None),  # the mail it came from
                       "talk": self._talk("task", t["key"])} for t in self._open_tasks()],
            "ideas": [{"n": i["n"], "text": i["text"], "ts": i["ts"], "status": i.get("status") or "open",
                       "talk": self._talk("idea", str(i["n"]))}
                      for i in reversed(self._ideas())],  # the newest first; open ones and those set aside
            "reports": reports,
            "domains": len(self._base.all_plans()),  # none yet: a new user, shown where to start
            "pending_confirmations": len(self._pending()),
            "unread": len(self._store.inbox(unread_only=True, limit=500)),
        }

    def alerts(self) -> list[dict[str, Any]]:
        """Unread alerts, one per topic: a state that keeps being true (still
        working after 23:30, still behind on a goal) shows once, as its latest
        alert, carrying the ids of the earlier ones so reading it reads them
        all. Alerts about goals or deadlines no longer in the plan are left out."""
        live = {f"{p.id}:{kind}:{x.id}" for p in self._base.all_plans() for kind, xs in (("goal", p.goals), ("deadline", p.deadlines))
                for x in xs}
        decided = self._decisions()
        # the late-night anomaly and a "no work after HH:MM" goal are the same topic
        late = {f"{p.id}:goal:{g.id}" for p in self._base.all_plans() for g in p.goals if g.metric == "late_activity"}
        groups: dict[str, list[InboxItem]] = {}
        for i in self._store.inbox(limit=300, unread_only=True):
            if i.kind != "alert":
                continue
            key = i.signal_key or f"alert:{i.id}"
            parts = key.split(":")
            if len(parts) == 3 and parts[1] in ("goal", "deadline") and (
                    key not in live or decided.get(f"{parts[0]}/{parts[1]}:{parts[2]}") == "rejected"):
                self._store.mark_read([i.id])  # its goal is gone: nothing left to remind
                continue
            topic = "late-night" if key.startswith("anomaly:late-night") or key in late else key
            groups.setdefault(topic, []).append(i)
        out = []
        for key, items in groups.items():
            i = max(items, key=lambda x: x.ts)
            out.append({"id": i.id, "ids": [x.id for x in items], "times": len(items), "key": i.signal_key or key, "ts": i.ts.isoformat(),
                        "domain": i.domain, "title": i.title, "action": (i.data.get("actions") or [None])[0],
                        "digest_only": i.data.get("digest_only", False), "quiet_hold": i.data.get("quiet_hold", False)})
        return sorted(out, key=lambda a: a["ts"], reverse=True)

    def prepared(self, pid: int) -> dict[str, Any] | None:
        p = self._store.get_prepared(pid)
        if p is None:
            return None
        plan = self._base.plan(p.domain)
        url = (t.url if plan and (t := plan.target(p.target)) else None) or p.data.get("posting", {}).get("url")
        if p.kind == "mail_reply":
            url = p.data.get("web_url") or f"https://mail.google.com/mail/u/0/#all/{p.data['mail']['thread_id']}"
        deadline = None
        if plan:
            for st in deadline_statuses(plan, self._clock.now(), self._store.completed()):
                if p.target in st.pending_targets:
                    deadline = {"title": st.deadline.title, "days_left": st.days_left}
                    break
        return {"id": p.id, "status": p.status, "kind": p.kind, "domain": p.domain, "target": p.target, "title": p.title,
                "prepared_by": p.prepared_by, "prepared_by_label": self._label(p.prepared_by), "ts": p.ts.isoformat(),
                "url": url, "deadline": deadline, **p.data}

    def today(self) -> dict[str, Any]:
        now = self._clock.now()
        plans = self._base.plans()
        agenda = build_agenda(plans, now, self._store, self._metrics, self._store.completed(), self._hours, words=self.words)
        domains = []
        all_plans = {p.id: p for p in self._base.all_plans()}
        decided = self._decisions()
        for a in agenda.domains:
            d = agenda_data(a, self.words)
            # progress shows every goal; unconfirmed ones are marked and never nag
            full = all_plans.get(a.plan.id, a.plan)
            goals = []
            for g in full.goals:
                gp = self._base.goal_progress(full, g, now)
                if gp is None:
                    continue
                row = goal_data(gp, self.words)
                row["pending"] = decided.get(review_id(full.id, "goal", g.id)) != "confirmed"
                goals.append(row)
            d["goals"] = goals
            d["tracker"] = [
                {
                    "target": i.target,
                    "title": a.plan.target_title(i.target),
                    "status": i.status,
                    "applied": i.applied.isoformat() if i.applied else None,
                    "next_action": i.next_action,
                    "next_action_due": i.next_action_due.isoformat() if i.next_action_due else None,
                    "url": (t.url if (t := a.plan.target(i.target)) else None),
                }
                for i in a.plan.tracker
            ]
            d["hour_goals"] = [{"id": f"{a.plan.id}/{g.id}", "title": g.title} for g in a.plan.goals if g.metric == "hours"]
            domains.append(d)
        return {
            "now": now.isoformat(),
            "day": agenda.day.isoformat(),
            "domains": domains,
            "domain_choices": [{"id": d, "title": t} for d, t in self._base.domains(plans)],
            "project_todos": self._todos(),
            "statuses": ALL_STATUSES,
            "status_labels": status_labels(self.words),
            "unread": len(self._store.inbox(unread_only=True, limit=500)),
            "pending_confirmations": len(self._pending()),
        }
