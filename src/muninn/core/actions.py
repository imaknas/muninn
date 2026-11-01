"""How each thing Muninn did is taken back.

Everything Muninn does on its own is logged as an ActionRecord whose `undo`
holds what reversing it needs, tagged with a kind. Each kind is one class
here, registered by that kind string (the strings stored in existing records,
so old records keep undoing), that reads its stored fields and knows how to
reverse it. Adding a kind is adding a class; nothing branches on kinds.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Callable, ClassVar, Protocol

from . import plan_edits
from .models import ActionRecord, Confirmation, status_labels
from .timewin import local
from .ports import ActionLog, ConfirmationStore, ObservationStore, PreparedStore, StateStore
from .language import Language


class UndoStorage(PreparedStore, StateStore, ObservationStore, Protocol):
    """The records a reversal touches directly: a prepared item, the window labels, offline time."""


class UndoContext(Protocol):
    """What reversing needs from the rest of Muninn (the engine provides it)."""

    store: UndoStorage
    plans: Any
    calendar: Any
    tasks: Any
    words: Language
    AUTO_TAG: str

    def undo_status(self, change_id: int) -> bool: ...

    def delete_auto_event(self, event_id: str) -> None: ...

    def correct_hours(self, goal: str, hours: float, day: date | None, note: str = "") -> Any: ...

    def set_done(self, qualified_id: str, done: bool = True) -> None: ...

    def decide(self, rid: str, decision: str | None) -> None: ...

    def relabel_window(self, key: str) -> None: ...


def _edit_plan(ctx: Any, domain: str, edit: dict[str, Any]) -> Any:
    """A stored plan edit made on the plan; the inverse edit. What does not fit the plan is said in the user's
    language (the plans adapter that applies it has none)."""
    try:
        return ctx.plans.edit(domain, plan_edits.from_dict(edit))
    except plan_edits.Refused as e:
        raise ValueError(e.say(ctx.words)) from None


class Reversal(Protocol):
    kind: ClassVar[str]

    def run(self, ctx: UndoContext) -> bool:
        """Takes it back; False when it cannot be taken back (now)."""
        ...


@dataclass(frozen=True)
class DismissPrepared:
    id: int
    kind: ClassVar[str] = "dismiss_prepared"

    def run(self, ctx):
        ctx.store.set_prepared_status(int(self.id), "dismissed")
        return True


@dataclass(frozen=True)
class RestorePrepared:
    """A prepared item set aside: back to ready."""

    id: int
    kind: ClassVar[str] = "restore_prepared"

    def run(self, ctx):
        ctx.store.set_prepared_status(int(self.id), "ready")
        return True


@dataclass(frozen=True)
class PacketSent:
    """A packet marked sent: ready again, and the target's status as it was."""

    id: int
    change: int | None = None
    kind: ClassVar[str] = "packet_sent"

    def run(self, ctx):
        if self.change is not None and not ctx.undo_status(int(self.change)):
            return False  # the status could not go back: leave the packet as sent rather than half undone
        ctx.store.set_prepared_status(int(self.id), "ready")
        return True


@dataclass(frozen=True)
class PlaceUnnamed:
    """A place named in the app: removed, or what that name meant before put back."""

    place: str
    prev: dict[str, Any] | None = None
    kind: ClassVar[str] = "place_named"

    def run(self, ctx):
        saved = [p for p in (ctx.store.get_state("places") or []) if p["name"] != self.place]
        ctx.store.set_state("places", saved + ([self.prev] if self.prev else []))
        return True


@dataclass(frozen=True)
class AlertsUnread:
    """Reminders marked seen: shown again."""

    ids: list[int]
    kind: ClassVar[str] = "alerts_unread"

    def run(self, ctx):
        ctx.store.mark_unread([int(i) for i in self.ids])
        return True


@dataclass(frozen=True)
class AskRestored:
    """A conversation's question that Muninn let go of: asked again."""

    thread: str
    ask: dict[str, Any]
    kind: ClassVar[str] = "ask_settled"

    def run(self, ctx):
        return ctx.conversation().restore_ask(self.thread, dict(self.ask))


@dataclass(frozen=True)
class IdeaForgotten:
    """An idea the user said: let go of again."""

    n: int
    kind: ClassVar[str] = "idea_captured"

    def run(self, ctx):
        ctx.ideas.forget(int(self.n))
        return True


@dataclass(frozen=True)
class IdeaUnsettled:
    """An idea set aside, dropped or taken back up: as it was before."""

    n: int
    prev: str
    kind: ClassVar[str] = "idea_settled"

    def run(self, ctx):
        ctx.ideas.set_status(int(self.n), str(self.prev))
        return True


@dataclass(frozen=True)
class SourcesUnassigned:
    """What was said to count toward a domain: back to what each counted toward before."""

    repos: list
    apps: list
    places: list  # each place as it was saved before
    kind: ClassVar[str] = "sources_assigned"

    def run(self, ctx):
        # the rules as they were, even toward a domain put away since (it counts again if that comes back)
        for repo, prev in self.repos:
            if prev:
                ctx.store.learn_rule("repo", repo, prev, None)
            else:
                ctx.store.forget_rule("repo", repo)
            ctx.reattribute_repo(repo)
        for app, prev in self.apps:
            if prev:
                ctx.store.learn_rule("app", app, prev, None)
            else:
                ctx.store.forget_rule("app", app)
        for p in self.places:
            ctx.whereabouts.put_back(p)
        ctx.reattribute_unattributed()
        return True


@dataclass(frozen=True)
class StatusChange:
    id: int
    kind: ClassVar[str] = "status_change"

    def run(self, ctx):
        return bool(ctx.undo_status(int(self.id)))  # False when the change record is gone


@dataclass(frozen=True)
class CalendarEvent:
    """A block Muninn put on its calendar: removed."""

    id: str
    kind: ClassVar[str] = "calendar_event"

    def run(self, ctx):
        ctx.delete_auto_event(str(self.id))
        return True


@dataclass(frozen=True)
class CalendarRestore:
    """A block Muninn cancelled: put back where it was."""

    title: str
    start: str
    end: str
    kind: ClassVar[str] = "calendar_restore"

    def run(self, ctx):
        if ctx.calendar is None:
            return False
        ctx.calendar.create(self.title, datetime.fromisoformat(self.start), datetime.fromisoformat(self.end), ctx.AUTO_TAG)
        return True


@dataclass(frozen=True)
class CalendarMove:
    """A block Muninn moved: the new one removed, the old one put back."""

    id: str
    title: str
    start: str
    end: str
    kind: ClassVar[str] = "calendar_move"

    def run(self, ctx):
        if ctx.calendar is None:
            return False
        ctx.delete_auto_event(str(self.id))
        ctx.calendar.create(self.title, datetime.fromisoformat(self.start), datetime.fromisoformat(self.end), ctx.AUTO_TAG)
        return True


@dataclass(frozen=True)
class Hours:
    goal: str
    hours: float
    date: str | None = None
    kind: ClassVar[str] = "hours"

    def run(self, ctx):
        day = date.fromisoformat(self.date) if self.date else None
        ctx.correct_hours(self.goal, -float(self.hours), day, "undo")
        return True


@dataclass(frozen=True)
class DeadlineDone:
    id: str
    kind: ClassVar[str] = "deadline_done"

    def run(self, ctx):
        ctx.set_done(self.id, False)
        return True


@dataclass(frozen=True)
class PlanEdit:
    """`edit` is the stored inverse edit."""

    domain: str
    edit: dict[str, Any]
    kind: ClassVar[str] = "plan_edit"

    def run(self, ctx):
        _edit_plan(ctx, self.domain, self.edit)
        return True


@dataclass(frozen=True)
class Review:
    id: str
    kind: ClassVar[str] = "review"

    def run(self, ctx):
        ctx.decide(self.id, None)
        return True


@dataclass(frozen=True)
class ReviewMany:
    """Several plan items confirmed at once: all waiting again."""

    ids: list[str]
    kind: ClassVar[str] = "review_many"

    def run(self, ctx):
        for rid in self.ids:
            ctx.decide(rid, None)
        return True


@dataclass(frozen=True)
class ReviewEdit:
    id: str
    domain: str
    edit: dict[str, Any]
    kind: ClassVar[str] = "review_edit"

    def run(self, ctx):
        _edit_plan(ctx, self.domain, self.edit)
        ctx.decide(self.id, None)
        return True


@dataclass(frozen=True)
class TaskChange:
    """task_status, task_merge and task_due: the task registry reverses its own."""

    data: dict[str, Any] = field(default_factory=dict)
    kind: ClassVar[str] = "task"

    def run(self, ctx):
        return bool(ctx.tasks.undo(self.data))


@dataclass(frozen=True)
class WindowLabel:
    key: str
    prev: dict[str, Any] | None = None
    kind: ClassVar[str] = "window_label"

    def run(self, ctx):
        known = dict(ctx.store.get_state("window_classes") or {})
        if self.prev is None:
            known.pop(self.key, None)
        else:
            known[self.key] = self.prev
        ctx.store.set_state("window_classes", known)
        ctx.relabel_window(self.key)
        return True


@dataclass(frozen=True)
class Offline:
    """Time away from the keyboard counted toward a goal: counts toward nothing."""

    start: str
    kind: ClassVar[str] = "offline"

    def run(self, ctx):
        at = datetime.fromisoformat(self.start)
        for o in ctx.store.observations(at - timedelta(minutes=1), at + timedelta(minutes=1), kinds=("offline",)):
            ctx.store.reattribute(o.key, None, None, o.sensitivity)
        return True


@dataclass(frozen=True)
class DomainUncreated:
    """A domain created: put away again (what was recorded in it stays, archived)."""

    id: str
    source: str = ""
    kind: ClassVar[str] = "domain_created"

    def run(self, ctx):
        ctx.plans.archive(self.id)
        ctx.collect()
        ctx.plans_actor.uncreated(self.id, self.source)
        return True


@dataclass(frozen=True)
class DomainRestored:
    """A domain archived: back."""

    id: str
    kind: ClassVar[str] = "domain_archived"

    def run(self, ctx):
        ctx.plans.restore(self.id)
        ctx.collect()
        return True


@dataclass(frozen=True)
class RequestNotLive:
    """A request marked live by its commit: not live again, and that commit no longer counts for it."""

    id: int
    commit: str
    kind: ClassVar[str] = "request_live"

    def run(self, ctx):
        return ctx.requests.not_live(int(self.id), refused=str(self.commit))


REVERSALS: dict[str, type] = {c.kind: c for c in (AskRestored, DismissPrepared, RestorePrepared, PacketSent, PlaceUnnamed, AlertsUnread, IdeaForgotten, IdeaUnsettled, SourcesUnassigned, StatusChange, CalendarEvent, CalendarRestore, CalendarMove,
                                                  Hours, DeadlineDone, PlanEdit, Review, ReviewEdit, WindowLabel, Offline, DomainUncreated, DomainRestored, ReviewMany,
                                                  RequestNotLive)}
TASK_KINDS = ("task_status", "task_merge", "task_due")


def reversal(undo: dict[str, Any]) -> Reversal | None:
    """The reversal a stored undo describes, or None when it describes none
    (an action that cannot be taken back, or a kind no longer known)."""
    kind = undo.get("kind")
    if kind in TASK_KINDS:
        return TaskChange(dict(undo))
    cls = REVERSALS.get(kind)
    if cls is None:
        return None
    names = {f.name for f in fields(cls)}
    try:
        return cls(**{k: v for k, v in undo.items() if k in names})
    except TypeError:  # a required field missing: a malformed record
        return None


# ── doing: what may be done at once, held, or asked first ─────────────────────


class Reach(Enum):
    INTERNAL = "internal"  # changes only Muninn's own records; undo takes it all back
    OUTWARD_HELD = "outward_held"  # also tells someone, through something that can be withdrawn before it leaves
    OUTWARD_FINAL = "outward_final"  # sends something that cannot be taken back once out


class DecidedBy(Enum):
    USER = "user"  # the user's own words or tap
    INFERRED_SURE = "inferred_sure"  # Muninn's judgment, confident
    INFERRED_UNSURE = "inferred_unsure"  # Muninn's judgment, unsure
    AGENT = "agent"  # another agent: a project, a lent session


@dataclass(frozen=True)
class Applied:
    text: str  # the line that says what was done, kept with its undo
    domain: str
    undo: dict[str, Any]  # a stored reversal (see REVERSALS); {} when it cannot be taken back


@dataclass(frozen=True)
class Ran:
    """What the gate did: an action record (id, text), or a question (confirm_id)."""

    id: int | None
    text: str
    confirm_id: int | None = None


class Action(Protocol):
    name: ClassVar[str]
    reach: ClassVar[Reach]

    def preview(self, ctx: Any) -> str:
        """What it will do, said before doing it (a question card shows this)."""
        ...

    def apply(self, ctx: Any, hold: timedelta | None) -> Applied | None:
        """Does it (outward parts held `hold` long, when set); None when there is nothing to do."""
        ...


@dataclass(frozen=True)
class TaskClose:
    """A task done or dropped; the projects that sent it hear of it."""

    key: str
    status: str = "done"  # done | dropped
    why: str = ""
    by: str = "user"
    # the why came from a lent session's content: only these projects may hear it (None: anyone may)
    only_to: tuple[str, ...] | None = None
    claim: str | None = None  # the project's claim this settles on
    confirmed: int | None = None  # the question the user said yes to, when it was asked first
    mandate: str | None = None  # the user's standing permission it is settled under, when not asked
    name: ClassVar[str] = "task_close"
    reach: ClassVar[Reach] = Reach.OUTWARD_HELD

    def preview(self, ctx):
        t = ctx.tasks.get(self.key)
        return ctx.words("actions.task_closed", status=self.status, n=t["n"], title=t["title"][:50]) if t else self.key

    def question(self, ctx) -> tuple[str, tuple[str, str]]:
        t = ctx.tasks.get(self.key)
        words = ctx.words
        what = words("actions.task_what", n=t["n"], title=t["title"][:60]) if t else self.key
        if self.status == "done":
            return words("actions.task_ask_done", what=what), (words("actions.task_ask_done_yes"), words("actions.task_ask_done_no"))
        return words("actions.task_ask_still", what=what), (words("actions.task_ask_still_yes"), words("actions.task_ask_still_no"))

    def apply(self, ctx, hold):
        done = ctx.tasks.change_status(self.key, self.status, by=self.by, why=self.why, hold=hold, only_to=self.only_to,
                                       confirmed=self.confirmed, claim=self.claim, mandate=self.mandate)
        if done is None:
            return None
        t, prev = done
        return Applied(ctx.words("actions.task_closed", status=self.status, n=t["n"], title=t["title"][:50]), "",
                       {"kind": "task_status", "key": t["key"], "prev": prev})


@dataclass(frozen=True)
class TaskDue:
    key: str
    due: str
    name: ClassVar[str] = "task_due"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        t = ctx.tasks.get(self.key)
        return ctx.words("actions.task_due", n=t["n"], due=self.due) if t else self.key

    def apply(self, ctx, hold):
        done = ctx.tasks.change_due(self.key, self.due)
        if done is None:
            return None
        t, prev = done
        return Applied(ctx.words("actions.task_due", n=t["n"], due=t["due"]), "", {"kind": "task_due", "key": t["key"], "prev": prev})


@dataclass(frozen=True)
class PlanChange:
    """A change to a domain's plan (plan_edits), undone by its inverse edit."""

    domain: str
    edit: dict[str, Any]  # plan_edits.to_dict
    name: ClassVar[str] = "plan_change"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.describe_plan_edit(self.domain, plan_edits.from_dict(self.edit))

    def apply(self, ctx, hold):
        text = self.preview(ctx)
        inverse = _edit_plan(ctx, self.domain, self.edit)
        ctx.collect()
        return Applied(text, self.domain, {"kind": "plan_edit", "domain": self.domain, "edit": plan_edits.to_dict(inverse)})


@dataclass(frozen=True)
class TrackerStatus:
    """An application's status in the tracker, e.g. from a confirmation or interview mail."""

    domain: str
    target: str
    status: str
    because: str = ""  # what it is based on, said on the card and in the done line
    line: str = ""  # the done line exactly, when the caller words it (the user's own command)
    name: ClassVar[str] = "tracker_status"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        if self.line:
            return self.line
        label = status_labels(ctx.words).get(self.status, self.status)
        if self.because:  # it already names the company
            return ctx.words("actions.tracker_status_because", because=self.because, label=label)
        return ctx.words("actions.tracker_status", target=ctx.target_title(self.domain, self.target), label=label)

    def apply(self, ctx, hold):
        text = self.preview(ctx)
        return Applied(text, self.domain, {"kind": "status_change", "id": ctx.set_status(self.domain, self.target, self.status)})


@dataclass(frozen=True)
class HoursCorrection:
    """Hours added to (or taken from) a goal's estimate."""

    goal: str  # "<domain>/<goal>"
    hours: float
    date: str | None = None  # YYYY-MM-DD; today when None
    name: ClassVar[str] = "hours_correction"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def _title(self, ctx) -> str:
        domain, _, gid = self.goal.partition("/")
        plan = ctx._plan(domain, include_unconfirmed=True)
        g = plan.goal(gid) if plan else None
        if g is None:
            raise ValueError(ctx.words("actions.no_goal", goal=self.goal))
        return g.title

    def preview(self, ctx):
        return f"{self._title(ctx)} {'+' if self.hours >= 0 else ''}{self.hours:g}h"

    def apply(self, ctx, hold):
        day = date.fromisoformat(self.date) if self.date else None
        if ctx.correct_hours(self.goal, self.hours, day, "via command") is None:
            return None
        return Applied(self.preview(ctx), self.goal.split("/")[0],
                       {"kind": "hours", "goal": self.goal, "hours": self.hours, "date": self.date})


@dataclass(frozen=True)
class DeadlineMarkedDone:
    """A deadline the user says is done (a manual one)."""

    deadline: str  # "<domain>/<deadline>"
    title: str
    name: ClassVar[str] = "deadline_marked_done"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.deadline_done", title=self.title)

    def apply(self, ctx, hold):
        ctx.set_done(self.deadline)
        return Applied(self.preview(ctx), self.deadline.split("/")[0], {"kind": "deadline_done", "id": self.deadline})


def _when(ctx, start: str, end: str | None = None) -> str:
    s = datetime.fromisoformat(start)
    return ctx.words("actions.when", day=s, start=local(s, ctx.tz), end=local(datetime.fromisoformat(end), ctx.tz) if end else None)


@dataclass(frozen=True)
class BlockPut:
    """A block Muninn puts on its calendar for the user."""

    title: str
    start: str  # ISO times
    end: str
    name: ClassVar[str] = "block_put"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.block_put", when=_when(ctx, self.start, self.end), title=self.title)

    def apply(self, ctx, hold):
        eid = ctx.calendar.create(f"Muninn · {self.title}", datetime.fromisoformat(self.start), datetime.fromisoformat(self.end),
                                  f"{ctx.words('actions.block_put_note')}\n{ctx.AUTO_TAG}")
        return Applied(self.preview(ctx), "muninn", {"kind": "calendar_event", "id": eid})


@dataclass(frozen=True)
class BlockMoved:
    """One of Muninn's blocks moved to another time: taken off, room found (its old time free
    again), put back where it was when there is none."""

    event_id: str
    event_title: str  # as on the calendar ("Muninn · …")
    start: str  # where it was (ISO)
    end: str
    date: str  # where to: YYYY-MM-DD, at `at` (HH:MM) or the first room from `earliest` on
    at: str | None
    earliest: str | None
    minutes: int
    name: ClassVar[str] = "block_moved"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.block_moving", title=self.event_title.removeprefix("Muninn · "), date=self.date, at=self.at or "").rstrip()

    def apply(self, ctx, hold):
        from .schedule import find_slot

        cal, title = ctx.calendar, self.event_title.removeprefix("Muninn · ")
        cal.delete(self.event_id)
        try:
            s, e = find_slot(cal, ctx.clock.now(), ctx.tz, ctx.hours, title, date.fromisoformat(self.date), self.at,
                             self.minutes, self.earliest, words=ctx.words)
        except ValueError:
            cal.create(self.event_title, datetime.fromisoformat(self.start), datetime.fromisoformat(self.end), ctx.AUTO_TAG)
            raise
        eid = cal.create(self.event_title, s, e, f"{ctx.words('actions.block_moved_note')}\n{ctx.AUTO_TAG}")
        return Applied(ctx.words("actions.block_moved", title=title, when=_when(ctx, s.isoformat())), "muninn",
                       {"kind": "calendar_move", "id": eid, "title": self.event_title, "start": self.start, "end": self.end})


@dataclass(frozen=True)
class BlockRemoved:
    """One of Muninn's blocks cancelled."""

    event_id: str
    event_title: str
    start: str
    end: str
    name: ClassVar[str] = "block_removed"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.block_removed", title=self.event_title.removeprefix("Muninn · "))

    def apply(self, ctx, hold):
        ctx.calendar.delete(self.event_id)
        return Applied(self.preview(ctx), "muninn", {"kind": "calendar_restore", "title": self.event_title,
                                                     "start": self.start, "end": self.end})


@dataclass(frozen=True)
class PreparePacket:
    """Prepare a packet for a target the fit check doubted (asked first, with the reason)."""

    domain: str
    target: str
    title: str = ""
    why: str = ""  # what the fit check found; the question card shows it
    name: ClassVar[str] = "prepare_packet"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.prepare_packet", title=self.title or self.target)

    def apply(self, ctx, hold):
        p = ctx.packets.prepare_anyway(self.domain, self.target)
        if p is None:
            return None
        return Applied(ctx.words("actions.packet_prepared", title=p.title), p.domain, {"kind": "dismiss_prepared", "id": p.id})


@dataclass(frozen=True)
class PacketSetAside:
    """A prepared packet (or reply draft) set aside: not prepared again for that target."""

    id: int
    name: ClassVar[str] = "packet_set_aside"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        p = ctx.packets.get(self.id)
        return ctx.words("actions.packet_set_aside", title=p.title) if p else str(self.id)

    def apply(self, ctx, hold):
        p = ctx.packets.set_aside(self.id)
        if p is None:
            return None
        text = ctx.words("actions.reply_set_aside", title=p.title) if p.kind == "mail_reply" else ctx.words("actions.packet_set_aside", title=p.title)
        return Applied(text, p.domain, {"kind": "restore_prepared", "id": p.id})


@dataclass(frozen=True)
class PacketSubmitted:
    """The user sent a packet: it is done and its target applied."""

    id: int
    name: ClassVar[str] = "packet_submitted"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        p = ctx.packets.get(self.id)
        return ctx.words("actions.packet_submitted", title=p.title) if p else str(self.id)

    def apply(self, ctx, hold):
        done = ctx.packets.mark_sent(self.id)
        if done is None:
            return None
        p, change = done
        text = ctx.words("actions.reply_handled", title=p.title) if p.kind == "mail_reply" else ctx.words("actions.packet_submitted", title=p.title)
        return Applied(text, p.domain, {"kind": "packet_sent", "id": p.id, "change": change})


@dataclass(frozen=True)
class PacketApply:
    """Sending a packet's filled application form: it cannot be taken back once out, so it is asked first (the question
    is the user's one press), and the target is marked applied only when the site says it was received."""

    id: int
    title: str = ""
    name: ClassVar[str] = "packet_apply"
    reach: ClassVar[Reach] = Reach.OUTWARD_FINAL

    def preview(self, ctx):
        return ctx.words("actions.packet_apply", title=self.title or str(self.id))

    def question(self, ctx) -> tuple[str, tuple[str, str]]:
        words = ctx.words
        return words("actions.packet_apply_ask", title=self.title or str(self.id)), (words("actions.packet_apply_yes"), words("actions.packet_apply_no"))

    def apply(self, ctx, hold):
        sent = ctx.applications.submit(self.id)
        if sent is None:
            return None  # the site stopped: the user was told what is left
        p, said = sent
        return Applied(ctx.words("actions.packet_applied", title=p.title, said=said), p.domain, {})


@dataclass(frozen=True)
class PlaceNamed:
    """A place the user named (where they were); undo puts back what was saved under that name before."""

    place: str
    lat: float
    lon: float
    name: ClassVar[str] = "place_named"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.place_naming", place=self.place)

    def apply(self, ctx, hold):
        prev = ctx.whereabouts.saved(self.place)
        p = ctx.whereabouts.write_place(self.place, self.lat, self.lon, None)
        return Applied(ctx.words("actions.place_named", place=p.name), "", {"kind": "place_named", "place": p.name, "prev": prev})


@dataclass(frozen=True)
class AlertsSeen:
    """Reminders the user saw: read; undo shows them again."""

    ids: tuple[int, ...]
    title: str = ""
    name: ClassVar[str] = "alerts_seen"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.alerts_seen", title=self.title)

    def apply(self, ctx, hold):
        if not self.ids:
            return None
        ctx.alerts.mark_read(self.ids)
        return Applied(self.preview(ctx), "", {"kind": "alerts_unread", "ids": list(self.ids)})


@dataclass(frozen=True)
class AskSettled:
    """What a conversation asked of the user is past (it went on in a deep talk since): no longer waiting; undo asks
    it again."""

    thread: str
    why: str
    name: ClassVar[str] = "ask_settled"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.ask_settling", why=self.why)

    def apply(self, ctx, hold):
        ask = ctx.conversation().settle_ask(self.thread)
        if ask is None:
            return None
        return Applied(ctx.words("actions.ask_settled", question=ask.get("text") or "", why=self.why), "",
                       {"kind": "ask_settled", "thread": self.thread, "ask": ask})


@dataclass(frozen=True)
class IdeaCaptured:
    """An idea the user said, kept; undo lets it go."""

    text: str
    called: str = ""  # the name it is known by, when one was given
    name: ClassVar[str] = "idea_captured"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.idea_capturing", text=self.text)

    def apply(self, ctx, hold):
        idea = ctx.ideas.add(self.text, self.called or None)
        called = idea["name"] if idea["name"] != f"idea-{idea['n']}" else ""  # a number is no name to say
        return Applied(ctx.words("actions.idea_captured", text=idea["text"], called=called), "",
                       {"kind": "idea_captured", "n": idea["n"], "name": idea["name"]})


@dataclass(frozen=True)
class IdeaSettled:
    """An idea set aside, dropped, or taken back up; undo puts it back as it was."""

    n: int
    to: str  # shelved | dropped | open
    text: str
    why: str = ""
    name: ClassVar[str] = "idea_settled"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.idea_settled", text=self.text, to=self.to)

    def apply(self, ctx, hold):
        prev = ctx.ideas.set_status(self.n, self.to, self.why)
        return Applied(self.preview(ctx), "", {"kind": "idea_settled", "n": self.n, "prev": prev})


@dataclass(frozen=True)
class DomainCreated:
    """A new domain from a drafted plan. Its goals and deadlines wait for the user in the plan
    review, item by item; undo archives it (whatever was recorded in it is kept)."""

    doc: dict[str, Any]  # the plan document (plan.yaml shape)
    source: str = ""  # what it grew out of, e.g. "idea:3" (whoever owns that hears of it, and of its undo)
    name: ClassVar[str] = "domain_created"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        dom = self.doc.get("domain") or {}
        return ctx.words("actions.domain_creating", title=dom.get("title") or dom.get("id"))

    def apply(self, ctx, hold):
        domain = ctx.plans.create(self.doc)
        ctx.collect()
        ctx.plans_actor.created(domain, self.source)
        return Applied(self.preview(ctx), domain, {"kind": "domain_created", "id": domain, "source": self.source})


@dataclass(frozen=True)
class DomainArchived:
    """A domain put away; undo brings it back."""

    domain: str
    title: str
    name: ClassVar[str] = "domain_archived"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.domain_archiving", title=self.title)

    def apply(self, ctx, hold):
        ctx.plans.archive(self.domain)
        ctx.collect()
        return Applied(self.preview(ctx), self.domain, {"kind": "domain_archived", "id": self.domain})


@dataclass(frozen=True)
class DomainReviewed:
    """Every item a domain's plan still waits on, confirmed at once (a drafted domain taken whole)."""

    domain: str
    title: str
    ids: tuple[str, ...]
    name: ClassVar[str] = "domain_reviewed"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.domain_reviewed", title=self.title, n=len(self.ids))

    def apply(self, ctx, hold):
        for rid in self.ids:
            ctx.decide(rid, "confirmed")
        return Applied(self.preview(ctx), self.domain, {"kind": "review_many", "ids": list(self.ids)})


@dataclass(frozen=True)
class SourcesAssigned:
    """Repos, apps and named places the user said count toward a domain: what was already
    recorded from them is attributed again; undo puts back what each counted toward before."""

    domain: str
    title: str
    repos: tuple[str, ...] = ()
    apps: tuple[str, ...] = ()
    places: tuple[str, ...] = ()
    name: ClassVar[str] = "sources_assigned"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.sources_assigned", sources=ctx.words.join([*self.repos, *self.apps, *self.places]), title=self.title)

    def apply(self, ctx, hold):
        learned = ctx.store.learned_rules()
        prev_repos = [[r, (learned.get(f"repo:{r}") or {}).get("domain")] for r in self.repos]
        prev_apps = [[a, (learned.get(f"app:{a}") or {}).get("domain")] for a in self.apps]
        prev_places = [p for p in (ctx.whereabouts.saved(name) for name in self.places) if p]
        for r in self.repos:
            ctx.set_repo_domain(r, self.domain)
        for a in self.apps:
            ctx.store.learn_rule("app", a, self.domain, None)
        for p in prev_places:
            ctx.whereabouts.write_place(p["name"], p["lat"], p["lon"], self.domain, p.get("radius_m", 150))
        ctx.reattribute_unattributed()
        return Applied(self.preview(ctx), self.domain,
                       {"kind": "sources_assigned", "repos": prev_repos, "apps": prev_apps, "places": prev_places})


@dataclass(frozen=True)
class RequestLive:
    """A request the running Muninn now includes (it contains the request's commit): live, decided by that commit;
    undo puts it back to not live."""

    id: int
    commit: str
    text: str
    name: ClassVar[str] = "request_live"
    reach: ClassVar[Reach] = Reach.INTERNAL

    def preview(self, ctx):
        return ctx.words("actions.request_live_preview", text=self.text, commit=self.commit[:7])

    def apply(self, ctx, hold):
        if ctx.requests.set_live(self.id, self.commit) is None:
            return None
        return Applied(ctx.words("actions.request_live", text=self.text, commit=self.commit[:7]), "",
                       {"kind": "request_live", "id": self.id, "commit": self.commit})


ACTIONS: dict[str, type] = {c.name: c for c in (TaskClose, TaskDue, PlanChange, TrackerStatus, HoursCorrection,
                                                  DeadlineMarkedDone, BlockPut, BlockMoved, BlockRemoved,
                                                  PreparePacket, PacketSetAside, PacketSubmitted, PacketApply, PlaceNamed, AlertsSeen, IdeaCaptured, IdeaSettled, SourcesAssigned,
                                                  DomainCreated, DomainArchived, DomainReviewed, RequestLive)}


def action_to_dict(a: Any) -> dict[str, Any]:
    return {"name": a.name, **asdict(a)}


def action_from_dict(d: dict[str, Any]) -> Any:
    cls = ACTIONS[d["name"]]
    names = {f.name for f in fields(cls)}
    return cls(**{k: (tuple(v) if isinstance(v, list) else v) for k, v in d.items() if k in names})


class Strategy(Protocol):
    def run(self, gate: ActionGate, action: Any, by: DecidedBy) -> Ran | None: ...


@dataclass(frozen=True)
class DoWithUndo:
    """Done now, listed with what was done and its undo; outward parts held `hold` long when set."""

    hold: timedelta | None = None

    def run(self, gate, action, by):
        return gate.do(action, self.hold)


@dataclass(frozen=True)
class AskFirst:
    """Asked as a card that shows exactly what would be done; done on yes."""

    def run(self, gate, action, by):
        return gate.ask(action, by)


HOLD_INFERRED = timedelta(hours=12)


def default_rules(reach: Reach, by: DecidedBy) -> Strategy:
    """Who decided × how far it reaches → how it is carried out."""
    if by is DecidedBy.INFERRED_UNSURE or reach is Reach.OUTWARD_FINAL:
        return AskFirst()
    if reach is Reach.OUTWARD_HELD and by is DecidedBy.INFERRED_SURE:
        return DoWithUndo(hold=HOLD_INFERRED)  # time to undo before anyone hears of it
    return DoWithUndo()


class GateStorage(ActionLog, ConfirmationStore, Protocol):
    """The actions done (with what reverses each), and the questions asked first."""


class ActionGate:
    """Actions go through here: the rules decide whether one is done now,
    done with its outward part held, or asked first; every undo goes through
    here too. So far task close and redate, plan changes and tracker status
    changes are Actions; the other kinds (calendar blocks, hours, deadlines
    done, review decisions, window labels, offline time) still log directly
    and move over one kind at a time. New code that acts adds an Action. `context` gives what actions act on (the
    engine), resolved when needed since the engine is built after the gate."""

    ASK = "action"  # the confirmation kind of a question the gate asks

    def __init__(self, store: GateStorage, clock: Any, context: Callable[[], Any],
                 rules: Callable[[Reach, DecidedBy], Strategy] = default_rules, *, words: Language):
        self.words = words
        self._store = store
        self._clock = clock
        self._context = context
        self._rules = rules

    def run(self, action: Any, by: DecidedBy) -> Ran | None:
        return self._rules(action.reach, by).run(self, action, by)

    def do(self, action: Any, hold: timedelta | None) -> Ran | None:
        applied = action.apply(self._context(), hold)
        if applied is None:
            return None
        rec = self._store.log_action(ActionRecord(None, self._clock.now(), applied.domain, applied.text, applied.undo))
        return Ran(rec.id, rec.text)

    def ask(self, action: Any, by: DecidedBy) -> Ran | None:
        """The user is asked first: in the action's own words when it has them (what is asked, and the two answers,
        doing it first), else whether to do what it would do."""
        ctx = self._context()
        text = action.preview(ctx)
        words = self.words
        question, answers = action.question(ctx) if hasattr(action, "question") else (
            words("actions.ask", text=text), (words("actions.yes"), words("actions.no")))
        c = self._store.add_confirmation(Confirmation(
            None, self._clock.now(), self.ASK, question, answers,
            {"action": action_to_dict(action), "by": by.value, **({"why": action.why} if getattr(action, "why", "") else {})},
            key=f"action:{json.dumps(action_to_dict(action), sort_keys=True)}"))
        return Ran(None, text, c.id) if c else None

    def answered(self, data: dict[str, Any], yes: bool, cid: int | None = None) -> Ran | None:
        """The user's answer to question `cid` the gate asked: yes is their decision, so done now (an action that
        records who decided keeps that the user confirmed it)."""
        action = action_from_dict(data["action"])
        if cid is not None and "confirmed" in {f.name for f in fields(action)}:
            action = replace(action, confirmed=cid)
        return self.do(action, None) if yes else None

    def undo(self, aid: int, on_undone: Callable[[str], None]) -> bool:
        a = self._store.get_action(aid)
        if a is None or a.undone:
            return False
        r = reversal(a.undo)
        if r is None or not r.run(self._context()):
            return False
        self._store.mark_action_undone(aid)
        on_undone(a.text)
        return True
