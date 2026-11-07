"""Interfaces the core depends on. Adapters implement them; main.py wires them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Iterable, Iterator, Protocol, Sequence

from .language import Language
from .location import AppSetup, LocationPoint
from .models import (
    ActionRecord,
    Attachment,
    CalendarEvent,
    Confirmation,
    DomainPlan,
    ExternalTodo,
    InboxItem,
    MailMessage,
    Observation,
    Posting,
    Prepared,
    Sensitivity,
    Turn,
)


class Clock(Protocol):
    def now(self) -> datetime:
        """Timezone-aware current time."""
        ...


class Source(Protocol):
    """Pulls new observations since `cursor` (opaque, source-defined JSON)."""

    name: str

    def collect(self, cursor: Any, now: datetime) -> tuple[list[Observation], Any]: ...


class PlanRepo(Protocol):
    def load(self) -> list[DomainPlan]: ...

    def set_tracker_status(
        self, domain: str, target: str, status: str, applied: date | None, *, replace_applied: bool = False
    ) -> dict[str, Any]:
        """Updates one tracker row and returns its previous {status, applied}.
        `applied` fills in a missing date, or with replace_applied sets it
        exactly (None removes it), which is how undo restores a row."""
        ...

    def edit(self, domain: str, edit: Any) -> Any:
        """Applies a plan edit (see plan_edits) to the domain's plan and
        tracker files; returns the edit that undoes it."""
        ...

    def create(self, doc: dict[str, Any]) -> str:
        """A new domain from a plan document (the plan.yaml shape); its id. ValueError when the
        document does not parse or a domain (live or archived) already has that id."""
        ...

    def archive(self, domain: str) -> None:
        """Puts a domain away: it is no longer loaded, and everything in it is kept."""
        ...

    def restore(self, domain: str) -> None:
        """Brings an archived domain back. ValueError when a live domain has taken its id."""
        ...


@dataclass(frozen=True)
class PushMessage:
    """What a notification says. With a title it names the thing ("Weekly
    applications behind: 0/4 this week"); without one, or for sensitive items, only the domain and
    a count. The payload is encrypted end to end (RFC 8291); the lock screen
    is the exposure, which the phone's preview setting controls."""

    domain: str
    count: int
    title: str | None = None
    sensitive: bool = False
    more: str = ""  # after the title, how many more there are, in the user's language (the composer says it)

    @property
    def text(self) -> str:
        if self.title and not self.sensitive:
            return f"{self.title}{self.more if self.count > 1 else ''}"
        noun = "reminder" if self.count == 1 else "reminders"
        return f"{self.domain}: {self.count} new {noun}"


class Channel(Protocol):
    """Outbound half of a channel. The inbound half (replies, quick input,
    confirmations) calls Engine methods directly from the channel's own server."""

    def send(self, messages: Sequence[PushMessage]) -> int:
        """Returns how many messages were delivered to at least one device."""
        ...


class CloudLoginLost(Exception):
    """A cloud backend's login has lapsed (expired, revoked, missing): what it would
    have answered is not answered by another model in its place."""


@dataclass(frozen=True)
class Capabilities:
    """What a model reads besides text. A text file the user sends is read as text by any model."""
    images: bool = False
    pdf: bool = False

    def reads(self, a: Attachment) -> bool:
        return self.images if a.is_image else self.pdf if a.media_type == "application/pdf" else True


TEXT_ONLY = Capabilities()


class Reply(str):
    """A model's reply, read as the text it is; what it took, when the provider says (tokens in and out, thinking
    counted as out), rides along for the record."""

    input_tokens: int | None
    output_tokens: int | None

    def __new__(cls, text: str, input_tokens: int | None = None, output_tokens: int | None = None) -> "Reply":
        r = super().__new__(cls, text)
        r.input_tokens, r.output_tokens = input_tokens, output_tokens
        return r


class LLMBackend(Protocol):
    name: str  # "<provider>:<model>", as cases and items keep it
    is_local: bool
    label: str  # what the user calls it ("local model", the provider's own name)
    login_hint: str  # how its login is put right when it lapses ("" when it has none)
    capabilities: Capabilities

    def available(self) -> bool: ...

    def check(self) -> str:
        """That it can answer (reached, logged in, the model there), without asking the model anything: a short
        detail, or an exception saying what is wrong."""
        ...

    def complete(self, system: str, prompt: str, *, json_mode: bool = False) -> str:
        """The reply; CloudLoginLost when the login has lapsed, any other exception for other failures.
        A backend that reads images or PDFs (`capabilities`) also takes `attachments`: only files it reads."""
        ...


class TranscriptSource(Protocol):
    """Conversation text since `cursor`: the user's own messages and the
    assistant's replies (never tool input or output)."""

    def collect(self, cursor: Any, now: datetime) -> tuple[list[Turn], Any]: ...


class Connection(Protocol):
    """An account Muninn acts through. 'relink' means the user must sign in again."""

    name: str

    def status(self) -> str: ...


class Calendar(Protocol):
    """The user's calendars: read busy time everywhere, write only in the
    calendar set aside for Muninn."""

    def available(self) -> bool: ...

    def busy(self, start: datetime, end: datetime) -> list[tuple[datetime, datetime]]: ...

    def own_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """Events in Muninn's own calendar."""
        ...

    def events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """The user's events in every calendar they show, except Muninn's own
        and the ones they declined, soonest first."""
        ...

    def create(self, title: str, start: datetime, end: datetime, description: str = "") -> str: ...

    def delete(self, event_id: str) -> None: ...


class LocationApp(Protocol):
    """A tracking app the user's phone sends its location from (core/location.py): one implementation per app."""

    name: str  # how it is named in the address it posts to (/api/ingest/location/<name>)
    label: str  # how the user knows it

    def points(self, body: Any) -> list[LocationPoint]:
        """The points in one post from the app (none for what is not a location: a status, a region event)."""
        ...

    def reply(self) -> Any:
        """The JSON body the app expects back."""
        ...

    def setup(self, url: str, token: str, words: Language) -> AppSetup:
        """How to set the app up to post to `url` with `token` (the ingest token), said in `words`."""
        ...


class Mailbox(Protocol):
    name: str
    kind: str  # personal | work

    def available(self) -> bool: ...

    def fetch(self, cursor: Any, now: datetime) -> tuple[list[MailMessage], Any]: ...

    def cursor_of(self, m: MailMessage) -> Any:
        """The cursor `fetch` goes on from after `m` (what a run that stopped part-way keeps)."""
        ...

    def get(self, message_id: str) -> MailMessage | None:
        """One message, read on demand (bodies are not kept by Muninn)."""
        ...

    def create_draft(self, reply_to: MailMessage, body: str) -> str:
        """Saves a reply as a draft in the mailbox. Never sends."""
        ...


class ProjectRecords(Protocol):
    """Project-level records kept elsewhere: the authority for project to-dos
    and decisions. Muninn reads them and never writes them."""

    def projects(self) -> set[str]:
        """Projects that keep such records."""
        ...

    def todos(self) -> list[ExternalTodo]: ...

    def state(self, project: str) -> str | None:
        """The project's current-state summary, if any."""
        ...

    def weekly_stats(self, iso_week: str) -> dict[str, Any] | None:
        """Conversation statistics for a week like '2026-W42', if produced."""
        ...


class NoProjectRecords:
    """When no external project records are configured."""

    def projects(self) -> set[str]:
        return set()

    def todos(self) -> list:
        return []

    def state(self, project: str) -> str | None:
        return None

    def weekly_stats(self, iso_week: str) -> dict[str, Any] | None:
        return None


# ── the project side: where the projects live, in a few small ports ─────────
# ProjectRecords (above) reads them; SessionRunner (below) lends a conversation to one. Muninn talks
# to the projects through a records system outside it, or through folders it reads itself: either
# side implements these, and the composition root picks one.


class HandoffOutbox(Protocol):
    def deliver(self, handoffs: Sequence[dict[str, Any]]) -> list[int]:
        """Hands pending handoffs (contracts/handoffs.v1) to their projects; the ids it delivered,
        which Muninn then marks delivered. A side the projects pull from (they fetch the pending
        handoffs and acknowledge each) delivers nothing here."""
        ...


class ProjectInbox(Protocol):
    def collect(self) -> list[dict[str, Any]]:
        """What the projects wrote to Muninn since the last look (contracts/messages.v1: questions,
        progress, done, tasks), each with its id, so a message seen twice is taken once. A side that
        posts its messages to Muninn's API has nothing to collect here."""
        ...


class SandboxRefused(Exception):
    """Why a sandbox could not be opened, renamed or made a project: taken (the name is another project's, or a
    sandbox for something else), bad_name (not a name that side takes), too_many (limit, current), dirty (changes
    not committed yet), busy (its session is in the middle of a turn: once it is done, again), unreachable, or
    failed."""

    def __init__(self, kind: str, *, limit: int | None = None, current: int | None = None, detail: str = ""):
        super().__init__(f"{kind}: {detail}" if detail else kind)
        self.kind = kind
        self.limit = limit
        self.current = current
        self.detail = detail


class Sandboxes(Protocol):
    def create(self, name: str, purpose: str) -> str | None:
        """Opens a project to explore an idea in (read-only until the user allows more): the
        project's name, which can then be lent like any project. Asked again with the same name and
        purpose (a retry), the same project. None when this side opens no sandboxes at all;
        SandboxRefused when it does but could not this time."""
        ...

    def upgrade(self, name: str, rename: str | None) -> str | None:
        """Makes the sandbox a real project (its history and files kept; renamed when asked): its name then.
        Asked again for one already made a project, its name (a retry is safe). None when this side cannot;
        SandboxRefused when it can but could not this time."""
        ...

    def rename(self, name: str, to: str) -> str | None:
        """Renames a sandbox or a project made of one, wherever it lives (what was lent to it goes on under the
        new name): its name then. Asked again once done, the same (a retry is safe). None when this side cannot;
        SandboxRefused when it can but could not this time."""
        ...


class PulledOutbox:
    """The projects fetch their pending handoffs themselves."""

    def deliver(self, handoffs: Sequence[dict[str, Any]]) -> list[int]:
        return []


class PostedInbox:
    """The projects post their messages to Muninn's API."""

    def collect(self) -> list[dict[str, Any]]:
        return []


class RunningVersion(Protocol):
    """Which version of Muninn is running, and what the deploys did: how a change is known to be live."""

    unknown: str | None  # why the running version cannot be told (said, never guessed); None when it can

    def head(self) -> str | None:
        """The commit running; None when it cannot be told."""
        ...

    def contains(self, commit: str) -> bool | None:
        """Whether the running version includes that commit; None when that cannot be told (a commit this history does
        not know, as after the history was started afresh, or no version at all)."""
        ...

    def deploy_lines(self) -> list[str]:
        """The deploy log as written, oldest first: "<date> <time> <from> -> <to>" for a deploy, "<date> <time>
        rollback <bad> -> <previous>: <reason>" for one rolled back."""
        ...


class UnknownVersion:
    """An install that cannot tell which version runs: nothing is taken to be live, and `unknown` says why."""

    def __init__(self, why: str):
        self.unknown = why

    def head(self) -> str | None:
        return None

    def contains(self, commit: str) -> bool | None:
        return None

    def deploy_lines(self) -> list[str]:
        return []


class NoSandboxes:
    """This side cannot open exploration projects."""

    def create(self, name: str, purpose: str) -> str | None:
        return None

    def upgrade(self, name: str, rename: str | None) -> str | None:
        return None

    def rename(self, name: str, to: str) -> str | None:
        return None


class PostingFetcher(Protocol):
    """Fetches a public posting. Returns None when this fetcher cannot read
    the URL, so several can be chained."""

    def fetch(self, url: str) -> Posting | None: ...


class DocumentSource(Protocol):
    """The user's own reference documents (e.g. resume versions) as text."""

    def documents(self) -> list[tuple[str, str]]: ...


class Classifier(Protocol):
    def classify_domain(self, description: dict[str, Any], domains: Sequence[tuple[str, str]]) -> str | None:
        """Suggests a domain id for an unattributed observation, or None."""
        ...


# ── storage: one small port per kind of record; Store is all of them (one database) ──


class ObservationStore(Protocol):
    """What the sources observed, and the domain each observation is attributed to."""

    def add_observations(self, observations: Iterable[Observation]) -> list[Observation]:
        """Inserts new ones (by key); returns only those that were new."""
        ...

    def observations(
        self,
        since: datetime,
        until: datetime | None = None,
        *,
        domain: str | None = None,
        goal: str | None = None,
        kinds: Sequence[str] | None = None,
    ) -> list[Observation]: ...

    def last_activity(self, domain: str) -> datetime | None: ...

    def unattributed(self, limit: int) -> list[Observation]: ...

    def observations_for_repo(self, repo: str) -> list[Observation]: ...

    def observations_for_window(self, app: str, title: str) -> list[Observation]:
        """Every focus of one window (title compared on its first 120 characters)."""
        ...

    def reattribute(self, key: str, domain: str | None, goal: str | None, sensitivity: Sensitivity) -> None: ...


class StateStore(Protocol):
    """Source cursors and small key/value state."""

    def get_state(self, key: str) -> Any: ...

    def set_state(self, key: str, value: Any) -> None: ...

    def update_state(self, key: str, change: Callable[[Any], Any]) -> Any:
        """`change(value now)` written back, nothing written between the two (what a value read, changed and written
        back uses: another process may write the same key). What was written."""
        ...


class ThreadStore(Protocol):
    """The conversations, kept for good: each thread's own fields (what it is about, when it was last
    talked in, the next steps it offered, the deep talk it is lent to) and its turns in order."""

    def thread(self, tid: str) -> dict[str, Any] | None:
        """The thread with its turns (`turns`), or None."""
        ...

    def save_thread(self, tid: str, t: dict[str, Any]) -> None:
        """Keeps the thread as given; turns are only ever added after the ones kept."""
        ...

    def update_thread(self, tid: str, change: Callable[[dict[str, Any] | None], dict[str, Any] | None]) -> dict[str, Any] | None:
        """`change(the thread now, or None)` kept (None: nothing), nothing written between the two: how a thread is
        changed, since another process may add to it meanwhile. What was kept."""
        ...

    def threads(self, limit: int, before: str | None = None) -> list[tuple[str, dict[str, Any]]]:
        """The latest threads talked in (before `before`, a ts), newest first, with their turns."""
        ...

    def threads_about(self, kind: str, ref: str) -> list[tuple[str, dict[str, Any]]]:
        """The threads about one item, newest first, with their turns."""
        ...

    def thread_stamps(self, limit: int) -> list[tuple[str, str, int]]:
        """The latest threads talked in, newest first: id, when last talked in, how many turns (no turns read)."""
        ...


class MarkerStore(Protocol):
    """Once-per-key markers for idempotent entry points."""

    def claimed(self, marker: str) -> bool: ...

    def claim(self, marker: str, at: datetime) -> bool:
        """True the first time a marker is claimed, False afterwards."""
        ...

    def release(self, marker: str) -> None: ...


class SignalStore(Protocol):
    """Signal dedupe: the level a signal was last raised at, per day."""

    def signal_level(self, key: str, day: date) -> int | None: ...

    def record_signal(self, key: str, day: date, level: int) -> None: ...


class InboxStore(Protocol):
    """The inbox."""

    def add_inbox(self, item: InboxItem) -> InboxItem: ...

    def inbox(self, *, limit: int = 100, unread_only: bool = False) -> list[InboxItem]: ...

    def get_inbox(self, item_id: int) -> InboxItem | None: ...

    def mark_read(self, ids: Sequence[int]) -> None: ...

    def mark_unread(self, ids: Sequence[int]) -> None: ...


class PushLog(Protocol):
    """Push accounting."""

    def pushes_by_tier(self, day: date) -> dict[str, int]:
        """Alert pushes already sent on `day`, per quota tier."""
        ...

    def record_push(self, day: date, at: datetime, domain: str, tier: str) -> None: ...


class ConfirmationStore(Protocol):
    """Questions put to the user, and their answers."""

    def add_confirmation(self, c: Confirmation) -> Confirmation | None:
        """Skips (returns None) if an unanswered confirmation with the same key exists."""
        ...

    def confirmations(self, *, pending_only: bool = True) -> list[Confirmation]: ...

    def answer_confirmation(self, cid: int, answer: str, at: datetime) -> Confirmation | None:
        """Records the answer to a still-open confirmation (the caller validates it)."""
        ...


class RuleStore(Protocol):
    """Attribution the user confirmed."""

    def learned_rules(self) -> dict[str, dict[str, Any]]: ...

    def shown_files(self) -> dict[str, str]:
        """The files deep talks showed the user, by id: the project each came from."""
        ...

    def learn_rule(self, kind: str, match: str, domain: str, goal: str | None) -> None: ...

    def forget_rule(self, kind: str, match: str) -> None: ...

    def forget_repo(self, repo: str) -> int:
        """Removes a repo name everywhere it was recorded; returns observations touched."""
        ...


class CompletionStore(Protocol):
    """Deadlines the user marked done."""

    def completed(self) -> set[str]: ...

    def set_completed(self, qualified_id: str, done: bool, at: datetime) -> None: ...


class SubscriptionStore(Protocol):
    """Push subscriptions."""

    def subscriptions(self) -> list[dict[str, Any]]: ...

    def add_subscription(self, sub: dict[str, Any], at: datetime, device: int | None = None) -> None:
        """Kept for the paired device it came from, in place of that device's earlier one: a device has one."""
        ...

    def subscribed(self, device: int) -> bool:
        """Whether Muninn pushes to that device."""
        ...

    def remove_subscription(self, endpoint: str) -> None: ...

    def remove_device_subscriptions(self, device: int) -> None: ...


class StatusChangeLog(Protocol):
    """Status changes made from the app, kept for undo."""

    def record_status_change(self, domain: str, target: str, before: dict[str, Any], after: dict[str, Any], at: datetime) -> int: ...

    def status_change(self, change_id: int) -> dict[str, Any] | None: ...


class PreparedStore(Protocol):
    """Things prepared for the user."""

    def add_prepared(self, p: Prepared) -> Prepared: ...

    def prepared(self, *, status: str | None = "ready") -> list[Prepared]: ...

    def get_prepared(self, pid: int) -> Prepared | None: ...

    def set_prepared_status(self, pid: int, status: str) -> None: ...


class ActionLog(Protocol):
    """Things done on the user's behalf, each with what reverses it."""

    def log_action(self, a: ActionRecord) -> ActionRecord: ...

    def actions(self, since: datetime) -> list[ActionRecord]: ...

    def get_action(self, aid: int) -> ActionRecord | None: ...

    def mark_action_undone(self, aid: int) -> None: ...


class TurnStore(Protocol):
    """Conversation text and what reflection made of it."""

    def add_turns(self, turns: Iterable[Turn]) -> int: ...

    def sessions_to_digest(self, limit: int) -> list[dict[str, Any]]:
        """Sessions with user turns newer than their last digest, newest first:
        {session, project, after (ts of last digest or None), until}."""
        ...

    def turns(self, session: str, after: datetime | None, until: datetime) -> list[Turn]: ...

    def save_digest(self, session: str, project: str | None, until: datetime, data: dict[str, Any], at: datetime) -> None: ...

    def digests(self, since: datetime, until: datetime) -> list[dict[str, Any]]: ...


class UsageStore(Protocol):
    """The tokens every model reply took, by backend: what a spending limit counts."""

    def add_usage(self, ts: datetime, backend: str, input_tokens: int, output_tokens: int) -> None: ...

    def usage_since(self, since: datetime) -> list[tuple[str | None, int, int]]:
        """Each backend's tokens in and out since then."""
        ...


class EvalStore(Protocol):
    """Model calls kept as evaluation cases, and the user's corrections to them."""

    def add_eval_case(self, ts: datetime, task: str, system: str, data: dict[str, Any], reply: str | None,
                      backend: str | None, *, by_local: bool, cloud_ok: bool, json_mode: bool, ms: int | None = None,
                      tokens: tuple[int | None, int | None] = (None, None)) -> int: ...

    def set_case_verdict(self, case_id: int, verdict: dict[str, Any]) -> None:
        """What a decision point decided on that case: {label, confidence, by}."""
        ...

    def add_case_feedback(self, case_id: int, feedback: dict[str, Any]) -> None: ...

    def eval_cases(self, task: str | None = None, limit: int = 1000) -> list[dict[str, Any]]: ...


class RecordStore(Protocol):
    """The Principal Handoff records the projects sent, kept as they came (core section 10's deduplication)."""

    def add_record(self, ext: str, record: dict[str, Any], at: datetime, canonical: Callable[[dict[str, Any]], str]) -> str:
        """Keeps the record, as it came, among those of the extension named `ext` (whatever its version): "new";
        already held: "duplicate"; its id held with other content: the greater canonical form is kept, this one
        ("replaced") or the one held ("kept"). `canonical` reads both in one version."""
        ...

    def records_of(self, ext: str, project: str, key: str) -> list[dict[str, Any]]:
        """Every record held for one item (`ext`: its extension by name), as each came, in arrival order."""
        ...


class HandoffStore(Protocol):
    """Handoffs: {id, ts, status, **fields}, status pending | delivered | done | cancelled."""

    def add_handoff(self, fields: dict[str, Any], at: datetime, *,
                    replaces: Callable[[dict[str, Any], dict[str, Any]], bool]) -> dict[str, Any]:
        """Adds a pending handoff, atomically, unless one is already pending
        for the same project, to-do `item` (or, without an item, the same
        `fact`) and `reply_to`. That one is returned instead, updated with
        `fields` when `replaces(fields, existing)`."""
        ...

    def handoffs(self, *, status: str | None = "pending", since: int = 0) -> list[dict[str, Any]]:
        """Oldest first; only ids above `since`; any status when None."""
        ...

    def handoff(self, hid: int) -> dict[str, Any] | None:
        """That one, whatever its status."""
        ...

    def rename_handoff_project(self, old: str, new: str) -> None:
        """Every handoff to project `old` is to `new`."""
        ...

    def handoffs_to(self, project: str, since: int = 0, key: str | None = None) -> list[dict[str, Any]]:
        """One project's handoffs above id `since`, any status, oldest first; only those about its item `key` when given."""
        ...

    def set_handoff_status(self, hid: int, status: str, at: datetime) -> dict[str, Any] | None:
        """The updated handoff, or None if there is no such id."""
        ...

    def prune_handoffs(self, before: datetime) -> int:
        """Deletes delivered and cancelled handoffs last changed before then."""
        ...


class ProjectMessageStore(Protocol):
    """Project messages: {id, ts, project, type, text, handoff_id, status, answer, context};
    type question | progress | done; status open | answered | dismissed | settled (questions) or received;
    context: what the message came out of (e.g. "lease:<id>", a lent conversation)."""

    def add_project_message(self, message: dict[str, Any], at: datetime) -> bool:
        """False if a message with that id was already stored."""
        ...

    def project_messages(self, *, type: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        """Oldest first."""
        ...

    def answer_project_message(self, mid: str, answer: str, at: datetime) -> dict[str, Any] | None:
        """Marks an open question answered; None if there is no open question with that id."""
        ...

    def settle_project_message(self, mid: str, how: str, at: datetime) -> dict[str, Any] | None:
        """Marks an open question settled elsewhere (`how`: what settled it); None if there is no open question with
        that id."""
        ...

    def amend_project_message(self, mid: str, text: str, at: datetime) -> bool:
        """An open question's words, as the project now asks it; False if there is none open with that id."""
        ...

    def dismiss_project_message(self, mid: str, at: datetime) -> dict[str, Any] | None:
        """Closes an open question unanswered; None if there is no open question with that id."""
        ...


class TaskStore(Protocol):
    """Tasks: {key, n, title, due, status (open | done | dropped), context, links, created, closed,
    closed_by, why, origins: [{project, ref, text}]}."""

    def add_task(self, key: str, fields: dict[str, Any], at: datetime) -> tuple[dict[str, Any], bool]:
        """The task with that key, and whether it was created now (False: it existed)."""
        ...

    def update_task(self, key: str, at: datetime, **fields: Any) -> dict[str, Any] | None: ...

    def task(self, key: str) -> dict[str, Any] | None:
        """By key, by an origin message id it took in, or by its short number ("T-12")."""
        ...

    def tasks(self, *, status: str | None = "open", project: str | None = None) -> list[dict[str, Any]]:
        """Soonest due first."""
        ...

    def add_task_origin(self, key: str, project: str, ref: str, text: str | None) -> None: ...

    def remove_task_origin(self, key: str, ref: str) -> None: ...

    def delete_task(self, key: str) -> None:
        """The task and its origin rows (after they were copied to the task it merged into)."""
        ...


class LeaseStore(Protocol):
    """Leases: a conversation thread lent to a project's session. {lease_id, thread, project, mode, state,
    started, ended, last_seq, last_activity, last_user_at, warn_state, warned_at, handover, end_reason, error,
    answering (1 from a message sent until the session's turn ends), left_at (when the user last spoke in another
    conversation)}; and the lent conversation's turns."""

    def add_lease(self, lease: dict[str, Any]) -> None: ...

    def lease(self, lease_id: str) -> dict[str, Any] | None: ...

    def leases(self, *, active: bool = True) -> list[dict[str, Any]]:
        """Active ones (not detached), or every one, newest first."""
        ...

    def update_lease(self, lease_id: str, **fields: Any) -> dict[str, Any] | None: ...

    def add_lease_turn(self, lease_id: str, role: str, text: str, at: datetime, files: Sequence[dict[str, str]] = (),
                       detail: str = "") -> None:
        """`files`: what the user sent with it ({id, name, media_type}), to show it again; `detail`: what a note
        (an answer role line) folds away under its one line."""
        ...

    def lease_turns(self, lease_id: str) -> list[dict[str, Any]]:
        """{role (user | session | answer | muninn), text, ts, files?, detail?}, oldest first."""
        ...


class PermissionStore(Protocol):
    """Permission requests a lent session's relay asks the user: {id, lease_id, project, tool,
    input_summary, risk, question, created, decided, decision, answers, by}."""

    def add_permission_request(self, req: dict[str, Any], at: datetime) -> dict[str, Any]:
        """Stores it unless that id was seen; returns the stored row either way."""
        ...

    def permission_request(self, rid: str) -> dict[str, Any] | None: ...

    def permission_requests(self, *, pending: bool = True) -> list[dict[str, Any]]: ...

    def decide_permission(self, rid: str, decision: str, answers: dict[str, str] | None, by: str,
                          at: datetime) -> dict[str, Any] | None:
        """Records the decision once; a request already decided keeps its first decision."""
        ...


class Store(
    ObservationStore,
    StateStore,
    ThreadStore,
    MarkerStore,
    SignalStore,
    InboxStore,
    PushLog,
    ConfirmationStore,
    RuleStore,
    CompletionStore,
    SubscriptionStore,
    StatusChangeLog,
    PreparedStore,
    ActionLog,
    TurnStore,
    EvalStore,
    HandoffStore,
    ProjectMessageStore,
    TaskStore,
    LeaseStore,
    PermissionStore,
    Protocol,
):
    """Every kind of record, as one database keeps them. Only the composition root
    and the engine name this; everything else names the small ports it uses."""


class RunnerError(Exception):
    """What the session runner refused or failed, by category (not_registered, cap_reached, busy,
    not_found, bad_request, agent_login (the session's agent is not logged in), rate_limit, process_crashed, resume_failed, handover_failed,
    unauthorized, internal, unreachable)."""

    def __init__(self, category: str, detail: str = ""):
        super().__init__(f"{category}: {detail}" if detail else category)
        self.category = category
        self.detail = detail


class Uploads(Protocol):
    """The files the user sends with what they say, kept on this machine."""

    def save(self, name: str, media_type: str, data: bytes) -> dict[str, Any]:
        """{id, name, media_type, size}."""
        ...

    def get(self, upload_id: str) -> Attachment | None: ...


class SessionRunner(Protocol):
    """Runs the project sessions a conversation is lent to (contracts/vendor/records/lease-runner.v2)."""

    def start(self, project: str, session_id: str, thread: str, *, resume: bool = False,
              mode: str = "discuss") -> dict[str, Any]:
        """The lease: {lease_id, project, mode, state}; mode as in set_mode."""
        ...

    def send(self, lease_id: str, text: str, client_msg_id: str, attachments: Sequence[Attachment] = ()) -> None:
        """What the user said, with the files they sent (saved in the project, images shown to the session)."""
        ...

    def events(self, lease_id: str, after_seq: int) -> Iterator[dict[str, Any]]:
        """The lease's events after that seq, as they come; ends when the connection does."""
        ...

    def keepalive(self, lease_id: str) -> None: ...

    def file(self, lease_id: str, file_id: str) -> tuple[bytes, str]:
        """A file the session showed the user (a `file` event): its bytes and type. RunnerError not_found once it
        is gone (kept 30 days)."""
        ...

    def terminal(self, lease_id: str) -> dict[str, str]:
        """Stops the session here; {command: what goes on with it in a terminal, hint: where to run it and after}."""
        ...

    def detach(self, lease_id: str, reason: str) -> None: ...

    def set_mode(self, lease_id: str, mode: str) -> dict[str, Any]:
        """discuss (read-only) or work (writes and commands, each asked of the user); only for the
        projects the policy lists in work_projects."""
        ...

    def policy(self) -> dict[str, Any]:
        """{attachable: [project], no_session_domains: [domain suffix]}"""
        ...
