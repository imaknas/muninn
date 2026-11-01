"""Domain-agnostic value types shared by the core and the adapters."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from .language import Language


class Sensitivity(enum.IntEnum):
    """Ordered so that max() picks the stricter level."""

    PUBLIC = 0
    PERSONAL = 1
    SENSITIVE = 2

    @classmethod
    def parse(cls, value: str | Sensitivity | None, default: Sensitivity) -> Sensitivity:
        if value is None:
            return default
        if isinstance(value, Sensitivity):
            return value
        return cls[value.upper()]

    @property
    def label(self) -> str:
        return self.name.lower()


@dataclass(frozen=True)
class Observation:
    """One fact observed by a source.

    `key` makes ingestion idempotent: re-collecting the same commit or the same
    transcript bucket never double-counts. `goal` is a qualified goal id
    ("<domain>/<goal>") when the observation counts toward a goal's metric.
    """

    source: str
    ts: datetime
    kind: str
    key: str
    payload: dict[str, Any] = field(default_factory=dict)
    domain: str | None = None
    goal: str | None = None
    sensitivity: Sensitivity = Sensitivity.SENSITIVE

    def with_attribution(
        self, domain: str | None, goal: str | None, sensitivity: Sensitivity
    ) -> Observation:
        return Observation(
            source=self.source,
            ts=self.ts,
            kind=self.kind,
            key=self.key,
            payload=self.payload,
            domain=domain,
            goal=goal,
            sensitivity=sensitivity,
        )


# ── commitments ─────────────────────────────────────────────────────────────


STATUS_ORDER = ["todo", "applied", "screening", "interview", "offer"]
CLOSED_STATUSES = {"rejected", "withdrawn"}
ALL_STATUSES = STATUS_ORDER + sorted(CLOSED_STATUSES)


def status_labels(words: Language) -> dict[str, str]:
    """Each status as the user reads it."""
    return {s: words("models.status", status=s) for s in ALL_STATUSES}


def status_at_least(status: str, threshold: str) -> bool:
    """Closed items count as past any threshold: there is nothing left to do."""
    if status in CLOSED_STATUSES:
        return True
    if status not in STATUS_ORDER or threshold not in STATUS_ORDER:
        return False
    return STATUS_ORDER.index(status) >= STATUS_ORDER.index(threshold)


@dataclass(frozen=True)
class Goal:
    id: str
    title: str
    metric: str
    target: float
    period: str  # "day" | "week"
    priority: int
    if_behind: tuple[str, ...] = ()
    private: bool = False
    params: dict[str, Any] = field(default_factory=dict)  # metric-specific keys, e.g. cutoff


@dataclass(frozen=True)
class DoneWhen:
    """Either `manual` (confirmed by the user) or a set of targets reaching a status."""

    manual: bool
    targets: tuple[str, ...] = ()
    status_at_least: str = "applied"


@dataclass(frozen=True)
class Deadline:
    id: str
    title: str
    due: date
    lead_days: int
    priority: int
    done_when: DoneWhen
    goal: str | None = None
    private: bool = False
    source: str | None = None  # who said so: the user, or what it was inferred from


@dataclass(frozen=True)
class Target:
    id: str
    title: str
    priority: int
    url: str | None = None
    notes: str | None = None  # human text: never leaves the machine


@dataclass(frozen=True)
class TrackerItem:
    """One row of a tracker file (e.g. a job application)."""

    target: str
    status: str
    title: str | None = None
    applied: date | None = None
    next_action: str | None = None
    next_action_due: date | None = None

    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED_STATUSES


@dataclass(frozen=True)
class DomainPlan:
    id: str
    title: str
    timezone: str
    sensitivity_default: Sensitivity
    priority: int = 3  # 1 is highest; decides who may interrupt and who wins conflicts
    goals: tuple[Goal, ...] = ()
    deadlines: tuple[Deadline, ...] = ()
    targets: tuple[Target, ...] = ()
    tracker: tuple[TrackerItem, ...] = ()
    idle_alert_days: int | None = None
    # Human text, only for the weekly report; never sent to any LLM.
    pivot: dict[str, Any] | None = None
    decision_tree: tuple[dict[str, Any], ...] = ()
    filters: tuple[str, ...] = ()  # human text rules (e.g. which targets qualify)

    @property
    def observe_only(self) -> bool:
        """A domain without goals is observed and summarized, never pushed."""
        return not self.goals

    def goal(self, goal_id: str) -> Goal | None:
        return next((g for g in self.goals if g.id == goal_id), None)

    def target(self, target_id: str) -> Target | None:
        return next((t for t in self.targets if t.id == target_id), None)

    def tracker_item(self, target_id: str) -> TrackerItem | None:
        return next((t for t in self.tracker if t.target == target_id), None)

    def target_title(self, target_id: str) -> str:
        t = self.target(target_id)
        if t:
            return t.title
        item = self.tracker_item(target_id)
        return (item.title if item and item.title else None) or target_id


# ── outputs ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Signal:
    """A drift finding. `level` grows as the situation worsens; the composer
    re-notifies within a day only when the level goes up."""

    key: str
    domain: str
    kind: str
    priority: int
    level: int
    title: str
    detail: str = ""
    actions: tuple[str, ...] = ()
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    domain_priority: int = 3
    # Being over an at-most limit (e.g. working past the cutoff) means the user
    # is awake and active right now, so quiet hours do not hold the push back.
    bypass_quiet: bool = False


@dataclass(frozen=True)
class InboxItem:
    id: int | None
    ts: datetime
    domain: str
    kind: str  # alert | brief | summary | weekly | info
    title: str
    body: str
    data: dict[str, Any] = field(default_factory=dict)
    signal_key: str | None = None
    level: int = 0
    read: bool = False


@dataclass(frozen=True)
class Confirmation:
    """A question waiting for the user's answer in the PWA."""

    id: int | None
    ts: datetime
    kind: str
    question: str
    options: tuple[str, ...]
    data: dict[str, Any] = field(default_factory=dict)
    answer: str | None = None
    key: str | None = None


@dataclass(frozen=True)
class Posting:
    """A public job posting (or any public page a packet is prepared for)."""

    url: str
    title: str
    location: str
    text: str


@dataclass(frozen=True)
class Prepared:
    """Something Muninn got ready so the user only has to review and act.
    status: ready | done | dismissed."""

    id: int | None
    ts: datetime
    domain: str
    target: str
    kind: str
    title: str
    data: dict[str, Any]
    status: str = "ready"
    prepared_by: str = ""  # which backend wrote it, shown to the user


@dataclass(frozen=True)
class ActionRecord:
    """Something Muninn did on its own. `undo` says how to reverse it."""

    id: int | None
    ts: datetime
    domain: str
    text: str
    undo: dict[str, Any] = field(default_factory=dict)
    undone: bool = False


@dataclass(frozen=True)
class Turn:
    """One message of a conversation the user had (with an assistant).
    role: user | assistant. Always sensitive: read only by the local model."""

    key: str
    session: str
    ts: datetime
    role: str
    project: str | None
    text: str


@dataclass(frozen=True)
class ExternalTodo:
    """A to-do kept by a project's own records (the authority for project
    work). owner: me | agent | pending."""

    project: str
    owner: str
    text: str


@dataclass(frozen=True)
class CalendarEvent:
    id: str
    start: datetime
    end: datetime
    title: str
    calendar: str = ""  # which calendar it is in (its name), for events read from all of them


@dataclass(frozen=True)
class MailMessage:
    """account_kind: personal | work (work mail is sensitive: local model unless routing is cloud)."""

    id: str
    thread_id: str
    account: str
    account_kind: str
    sender: str
    to: str
    subject: str
    ts: datetime
    text: str
    message_id: str = ""
    web_url: str = ""  # opens this message in the provider's web app


@dataclass(frozen=True)
class Attachment:
    """A file the user sent with what they said: a photo, a PDF, a text file."""

    name: str
    media_type: str
    data: bytes
    id: str = ""  # where it is kept (Uploads), to show it again

    @property
    def meta(self) -> dict[str, str]:
        """What a conversation keeps of it: enough to show it, not the file."""
        return {"id": self.id, "name": self.name, "media_type": self.media_type}

    @property
    def is_image(self) -> bool:
        return self.media_type.startswith("image/")
