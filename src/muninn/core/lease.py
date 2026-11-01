"""Lending a conversation to a project's session, and taking it back.

Muninn always owns the user's conversation. A thread can be lent to a session
of one project (read-only discussion; an idea's sandbox can also be switched to
work, where every write and command is asked first): while it is lent, what the user writes
goes to that session as it is, and Muninn's own understanding never reads it.
The session's turns are kept apart (lease_turns), so they never reach the
understanding model's history either; when the thread comes back, only the
handover summary (and what Muninn itself said beside the talk) joins the thread, marked local_only (the mark's old name: it means
lent content, which is never cleared and which only that session's projects may hear;
where a model reads it is the routing's to decide). The session itself is
run by the session runner (another system); Muninn keeps the lease's state and
reacts to its events.

Coming back: a lent conversation is given back without asking when the runner
says it has gone quiet, or when the user has moved on to another conversation
for a while; the session's handover joins the conversation.

What Muninn answers beside a talk (core/sidecar.py) stays beside it, except the one sentence of it that is the
project's: the session is told that as a message of its own (`relay`), and what Muninn said beside the talk joins
Muninn's own conversation once the talk is over.

Permission requests: a lent session's relay asks the user (a question the
session put, a web page outside the project's own sites, and in work mode a
file to write or a command to run). They wait on the
home page and push the user; inside quiet hours only while the user is
talking to that session (a message in the last ten minutes). Unanswered for
five minutes means denied.
"""

from __future__ import annotations

import re
import secrets
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Callable, Iterator, Protocol, Sequence
from urllib.parse import urlsplit

from .command_trust import CommandTrust
from .models import Attachment
from .ports import Channel, Clock, LeaseStore, PermissionStore, ProjectMessageStore, PushMessage, RunnerError, SessionRunner, StateStore
from .language import Language

ACTIVE_TALK = timedelta(minutes=10)  # the user counts as present this long after a message
WORKING = ("text_delta", "assistant", "tool_use", "tool_result", "turn_end", "question", "permission", "file")  # the session is at it
IDLE = ("pending", "held", "sent")  # the runner found it idle ("held"/"sent": rows from when the user was asked first)
SHOWN = ("project", "state", "mode", "warn_state", "error")  # what the screens show of a lease
SAID_AS = {"moved_on": "user"}  # an end reason the runner's contracts have no word for yet, as it is told
STUCK_AFTER = timedelta(minutes=5)  # a handover not written by then is given up on, for what waits on it
WAITING = "lease:waiting"  # talks gone into again while they wrote their handover, by lease: {since, files: [ids]}
RELAY = "lease:relay"  # what Muninn has yet to tell a talk (it was answering, or sending failed), by lease: [sentences]
REQUEST_TIMEOUT = timedelta(minutes=5)
POLICY_TTL = timedelta(minutes=5)
QUOTA_WARN = 0.8
STALE_AFTER_S = 3600  # a usage reading without its reset time is trusted this long
DECISIONS = ("allow", "deny", "allow_session")
ERRORS = {  # the runner's refusals by category, as the user is told them
    "not_registered": "lease.not_registered",
    "cap_reached": "lease.cap_reached",
    "busy": "lease.busy",
    "not_found": "lease.not_found",
    "agent_login": "lease.agent_login",
    "rate_limit": "lease.rate_limit",
    "unreachable": "lease.unreachable",
    "work_not_allowed": "lease.work_not_allowed",
    "runtime_unsupported": "lease.runtime_unsupported",
}
SAYS_WHY = {"busy", "runtime_unsupported"}  # refusals whose detail is the runner's own words for the user
MODE_NOTES = {"work": "lease.mode_work", "discuss": "lease.mode_discuss"}  # the modes, and how a switch to each is noted
PUSH_TITLES = {"ask_user": "lease.push_ask", "write_file": "lease.push_write", "run_command": "lease.push_run",
               "open_web": "lease.push_web"}


def friendly(e: RunnerError, *, words: Language) -> str:
    said = words(ERRORS[e.category]) if e.category in ERRORS else words("lease.trouble", category=e.category)
    return said + (f" {e.detail}" if e.category in SAYS_WHY and e.detail else "")


class LeaseStorage(LeaseStore, StateStore, ProjectMessageStore, Protocol):
    """The leases and their turns, the usage reading kept in state, and the questions talks left for the user."""


class PermissionStorage(PermissionStore, LeaseStore, Protocol):
    """The requests, and the lease each came from (to know whether the user is talking to it)."""


class ChannelLease:
    def __init__(self, store: LeaseStorage, clock: Clock, runner: SessionRunner, channel: Channel,
                 quiet: Callable[[datetime], bool], load_thread: Callable[[str], dict[str, Any] | None],
                 update_thread: Callable[[str, Callable[[dict[str, Any] | None], dict[str, Any] | None]], Any], *,
                 push_titles: bool = True,
                 share_uncleared: bool = False,
                 ended: Callable[[dict[str, Any], str | None], None] = lambda lease, report: None,
                 label: Callable[[str], str] = lambda project: project, words: Language):
        """`ended(lease, report)`: a lent conversation came back (once per talk), with its handover (`report`, as it
        joined the conversation; None when the talk broke off); called where the thread is written, after it is
        saved, so it only changes what Muninn keeps and asks no model. `label(project)`: what the user
        calls a project (an idea's sandbox by the idea's name), wherever a lease is shown or spoken of."""
        self.words = words
        self._store = store
        self._clock = clock
        self._runner = runner
        self._channel = channel
        self._quiet = quiet
        self._load = load_thread
        self._update_in = update_thread  # (tid, change): read, changed and kept with nothing written in between
        self._titles = push_titles  # False when the user wants pushes to say only how many
        self._share_uncleared = share_uncleared  # the user sends everything to the cloud model, uncleared words too
        self._policy: tuple[datetime, dict[str, Any]] | None = None
        self._serialize: Callable[[Callable[[], Any]], Any] = lambda fn: fn()
        self._changed: Callable[[str, dict[str, Any]], None] = lambda lease_id, lease: None
        self._files: Callable[[Sequence[str]], list[Attachment]] = lambda ids: []
        self._waiting_guard = threading.Lock()
        self._relay_guard = threading.Lock()  # what waits to be told, and Muninn's work beside each talk
        self._beside: dict[str, int] = {}  # by lease: Muninn's parts of what was said there, being done now
        self._told: Callable[[str, dict[str, Any]], None] = lambda lease_id, event: None
        self._ended = ended
        self.label = label

    def on_change(self, tell: Callable[[str, dict[str, Any]], None]) -> None:
        """`tell(lease_id, lease)`: what the screens show of a lease changed (its name, state, mode, whether it can
        act, that it is idle, an error), with the lease as they show it (`shown`); whatever changed it."""
        self._changed = tell

    def on_relay(self, tell: Callable[[str, dict[str, Any]], None]) -> None:
        """`tell(lease_id, event)`: a talk heard a sentence of what Muninn answered beside it ({type: relay, text}),
        for the views open on it."""
        self._told = tell

    def _update(self, lease_id: str, **fields: Any) -> dict[str, Any] | None:
        """Every change to a lease, so the screens hear of one in what they show, however it came about."""
        before = self._store.lease(lease_id)
        after = self._store.update_lease(lease_id, **fields)
        if after is not None and (before is None or any(before.get(k) != after.get(k) for k in SHOWN)):
            self._changed(lease_id, self.shown(after))
        return after

    def shown(self, lease: dict[str, Any]) -> dict[str, Any]:
        """A lease as the screens show it. Whether it can act is read from what the runner last said (no call)."""
        work = list((self._policy[1] if self._policy else {}).get("work_projects") or [])
        return {"lease_id": lease["lease_id"], "project": lease["project"], "label": self.label(lease["project"]),
                "thread": lease.get("thread"), "state": lease["state"],
                "mode": lease["mode"], "can_work": lease["project"] in work, "warn": lease["warn_state"] in IDLE,
                "error": lease.get("error")}

    def serialize_with(self, run: Callable[[Callable[[], Any]], Any]) -> None:
        """Thread writes run through this (the engine lock), so they never race the conversation's own saves;
        the runner's calls stay outside it, so a slow runner never holds up the app."""
        self._serialize = run

    def _update_thread(self, tid: str, change: Callable[[dict[str, Any]], None], create: bool = False) -> None:
        def changed(t: dict[str, Any] | None) -> dict[str, Any] | None:
            if t is None and not create:
                return None
            t = t or {"about": None, "turns": []}
            change(t)
            return t
        self._serialize(lambda: self._update_in(tid, changed))

    # ── policy (what the runner allows) ─────────────────────────────────────

    def policy(self, *, fresh: bool = False) -> dict[str, Any]:
        now = self._clock.now()
        if fresh or self._policy is None or now - self._policy[0] > POLICY_TTL:
            self._policy = (now, self._runner.policy())
        return self._policy[1]

    def _listed(self, key: str, project: str | None = None) -> list[str]:
        """A list from the policy; read again when `project` is not on it yet (a sandbox opened a moment ago)."""
        try:
            got = list(self.policy().get(key) or [])
            if project is not None and project not in got:
                got = list(self.policy(fresh=True).get(key) or [])
            return got
        except RunnerError:
            return []

    def attachable(self, project: str | None = None) -> list[str]:
        return self._listed("attachable", project)

    def can_work(self, project: str) -> bool:
        return project in self._listed("work_projects", project)

    def quota(self) -> dict[str, float]:
        """Latest utilization per window (0..1), leaving out readings that are stale: past the window's reset,
        or, when the reset time is unknown, older than an hour."""
        rl = self._store.get_state("lease:rate_limit") or {}
        now = self._clock.now().timestamp()

        def fresh(x: dict[str, Any]) -> bool:
            reset = x.get("resetsAt")
            return reset > now if isinstance(reset, (int, float)) else now - float(x.get("at") or 0) < STALE_AFTER_S

        return {w: float(x["utilization"]) for w, x in rl.items()
                if isinstance(x, dict) and x.get("utilization") is not None and fresh(x)}

    def quota_warning(self) -> str | None:
        high = {w: u for w, u in self.quota().items() if u >= QUOTA_WARN}
        if not high:
            return None
        labels = {w: str(x.get("label") or w) for w, x in (self._store.get_state("lease:rate_limit") or {}).items()
                  if isinstance(x, dict)}
        return self.words("lease.quota_used", used=self.words.join(
            self.words("lease.quota_window", window=labels.get(w, w), percent=round(u * 100)) for w, u in high.items()))

    # ── the user's side ─────────────────────────────────────────────────────

    def lease_for(self, thread_id: str | None) -> dict[str, Any] | None:
        t = self._load(thread_id) if thread_id else None
        return self._store.lease(t["lease"]) if t and t.get("lease") else None

    def attach(self, thread_id: str | None, project: str, *, confirm_quota: bool = False,
               attachments: Sequence[Attachment] = ()) -> dict[str, Any]:
        """Lends the conversation to a project's session. An idea's sandbox starts able to act (each write and
        command still asked first) and starts from what was said here, so the user never sets either up.
        A conversation that talked with this project before goes on with that same session (it remembers the
        talk, not only its handover), told only what was said here since. `attachments`: the files the user
        just sent, handed over with the first message."""
        if self.lease_for(thread_id):
            raise ValueError(self.words("lease.already_lent"))
        allowed = self.attachable(project)
        if project not in allowed:
            raise ValueError(self.words("lease.not_attachable", project=self.label(project),
                                        allowed=self.words.join(map(self.label, allowed)) or self.words("lease.none_reachable")))
        warning = self.quota_warning()
        if warning and not confirm_quota:
            return {"needs_confirm": self.words("lease.quota_confirm", warning=warning), "project": project, "label": self.label(project), "thread": thread_id}
        now = self._clock.now()
        tid = thread_id or secrets.token_hex(6)
        earlier = self._earlier(thread_id, project)
        session_id = earlier["lease_id"] if earlier else str(uuid.uuid4())
        mode = "work" if self.can_work(project) else "discuss"
        try:
            got = self._runner.start(project, session_id, tid, resume=earlier is not None, mode=mode)
        except RunnerError as e:
            raise ValueError(friendly(e, words=self.words)) from e
        lease_id = str(got.get("lease_id") or session_id)
        fresh = {"thread": tid, "project": project, "mode": mode, "state": str(got.get("state") or "attaching"),
                 "started": now.isoformat(), "last_activity": now.isoformat(), "last_user_at": now.isoformat()}
        if earlier is not None and lease_id == earlier["lease_id"]:  # the same lease again: what ended it is cleared
            self._update(lease_id, **fresh, **STRETCH)
        else:
            self._store.add_lease({"lease_id": lease_id, **fresh})
        def point(t: dict[str, Any]) -> None:
            t["lease"] = lease_id
            t["ts"] = now.isoformat()

        self._update_thread(tid, point, create=True)
        heard = self._last_heard(earlier) if earlier else None
        thread = self._load(tid)
        said = (opening(thread, project, self._lent_from, uncleared=self._share_uncleared, since=heard, words=self.words)
                or entering(thread, since=heard, uncleared=self._share_uncleared)
                or (self.words("lease.files_for_you") if attachments else None))
        opened = False
        # what it left undecided last time is its to ask, now that the user is in the talk again: not left waiting elsewhere
        unasked = left_by((thread or {}).get("talk_left"), project)
        if unasked:
            left = self.words("lease.left_undecided", questions="\n".join(f"- {q}" for q in unasked))
            said = f"{said}\n\n{left}" if said else left
        if said:
            try:
                self._runner.send(lease_id, said, secrets.token_hex(8), attachments)
                self._store.add_lease_turn(lease_id, "answer", self.words("lease.handed_over_files" if attachments else "lease.handed_over",
                                                                          project=self.label(project)), now)
                self._update(lease_id, answering=1)  # it starts on the opening at once
                opened = True
                if unasked:
                    self._update_thread(tid, lambda t: t.pop("talk_left", None))
            except RunnerError:
                pass  # lent all the same: the session knows the idea, and the user can say it again
        return {"thread": tid, "lease_id": lease_id, "project": project, "label": self.label(project), "state": got.get("state") or "attaching",
                "mode": mode, "can_work": self.can_work(project), "opened": opened, "resumed": earlier is not None}

    def _last_heard(self, lease: dict[str, Any]) -> str | None:
        """When a talk last heard anything of this conversation (its last line): what was said here after it is
        what it does not know."""
        turns = self._store.lease_turns(lease["lease_id"])
        return str(turns[-1].get("ts") or "") if turns else lease.get("started")

    def _earlier(self, thread_id: str | None, project: str) -> dict[str, Any] | None:
        """The last talk this conversation had with `project`, ended (its session can be resumed)."""
        if thread_id is None:
            return None
        return next((x for x in self._store.leases(active=False)
                     if x["thread"] == thread_id and x["project"] == project and x.get("ended")), None)

    def _lent_from(self, turn: dict[str, Any]) -> set[str]:
        """The projects a turn's lent content came from ("lease:<id>" in its via); empty when it does not say."""
        via = [turn["via"]] if isinstance(turn.get("via"), str) else turn.get("via") or []
        leases = [self._store.lease(v.split(":", 1)[1]) for v in via if str(v).startswith("lease:")]
        return {x["project"] for x in leases if x} if leases and all(leases) else set()

    def lend(self, thread_id: str, project: str, attachments: Sequence[Attachment] = ()) -> dict[str, Any]:
        """The one way into a talk: the conversation goes on with a project's session (an idea's sandbox when trying
        an idea out), which hears what it does not know of this conversation yet (`attach`); said in one line kept
        in the conversation. A talk still writing its handover is gone into once it has written it. It comes back by
        itself when it goes quiet or the user moves on."""
        lease = self.lease_for(thread_id) if thread_id else None
        if lease is not None and lease["project"] == project and lease["state"] == "detaching":
            self._wait(lease, attachments)
            text = self.words("lease.rejoin_after_handover", project=self.label(project))
            got = {"lease_id": lease["lease_id"], "project": project, "label": self.label(project), "thread": thread_id,
                   "state": "detaching", "mode": lease["mode"], "can_work": self.can_work(project), "text": text}
        else:
            got = self.attach(thread_id, project, attachments=attachments)
            if not got.get("lease_id"):
                return got
            got["text"] = lent_text(self.label(project), got.get("mode") == "work", bool(got.get("resumed")), words=self.words)
        now = self._clock.now().isoformat()
        self._update_thread(got["thread"], lambda t: t.update(turns=[*(t.get("turns") or []), {
            "role": "muninn", "text": got["text"], "ts": now, "note": True, "entered": got["lease_id"]}]))
        return got

    def files_from(self, load: Callable[[Sequence[str]], list[Attachment]]) -> None:
        """Where files sent earlier are found again by their ids (what was said while a handover was written
        waits with its files)."""
        self._files = load

    def _wait(self, lease: dict[str, Any], attachments: Sequence[Attachment]) -> None:
        """A talk gone into again while it writes its handover: gone into once it has (`resume_waiting`), with the
        files sent with what went into it."""
        with self._waiting_guard:
            waiting = self._store.get_state(WAITING) or {}
            mine = waiting.get(lease["lease_id"]) or {"since": self._clock.now().isoformat(), "files": []}
            mine["files"] = [*mine.get("files", []), *(a.id for a in attachments if a.id)]
            self._store.set_state(WAITING, {**waiting, lease["lease_id"]: mine})

    def _take_waiting(self, lease_id: str) -> dict[str, Any] | None:
        with self._waiting_guard:
            waiting = self._store.get_state(WAITING) or {}
            mine = waiting.pop(lease_id, None)
            if mine is not None:
                self._store.set_state(WAITING, waiting)
        return mine

    def resume_waiting(self, lease_id: str) -> str | None:
        """Once a talk gone into again has written its handover: gone into (`lend`), hearing what was said since it
        last heard this conversation, with the files. Why it could not be (said in the conversation too), else None.
        Calls the runner: never under the engine's lock."""
        lease = self._store.lease(lease_id)
        if lease is None or not lease.get("ended"):
            return None
        mine = self._take_waiting(lease_id)
        if mine is None:
            return None
        files = self._files(mine.get("files") or [])
        try:
            if len(files) < len(mine.get("files") or []):
                raise ValueError(self.words("lease.file_gone"))
            self.lend(lease["thread"], lease["project"], files)
        except ValueError as e:
            return self._not_given(lease, str(e))
        return None

    def sweep_waiting(self) -> list[tuple[str, str]]:
        """A talk gone into while it wrote its handover is never left waiting: once it has ended (however it
        ended), it is gone into (`resume_waiting`); a handover still not written after STUCK_AFTER is given up on
        and said so in the conversation. The leases that could not be gone into, with why. Calls the runner."""
        failed = []
        now = self._clock.now()
        for lease_id, mine in list((self._store.get_state(WAITING) or {}).items()):
            lease = self._store.lease(lease_id)
            if lease is None:
                self._take_waiting(lease_id)
                continue
            if lease.get("ended"):
                why = self.resume_waiting(lease_id)
            elif now - datetime.fromisoformat(mine["since"]) >= STUCK_AFTER:
                self._take_waiting(lease_id)
                why = self._not_given(lease, self.words("lease.handover_stuck"))
            else:
                continue
            if why is not None:
                failed.append((lease_id, why))
        return failed

    def _not_given(self, lease: dict[str, Any], why: str) -> str:
        """Says in the conversation that the talk could not be gone into again, and why."""
        self._update_thread(lease["thread"], lambda t: t.update(turns=[*(t.get("turns") or []), {
            "role": "muninn", "text": self.words("lease.not_rejoined", project=self.label(lease["project"]), why=why),
            "ts": self._clock.now().isoformat()}]))
        return why

    def renamed(self, old: str, new: str) -> None:
        """A project now goes by `new` (an idea's sandbox made a project): the talks lent to it, going on or given back,
        go on under that name, so coming back to one lends it to the project by its name now. The same name for
        both: only what the user calls it changed, and the screens open on a talk with it show that."""
        for lease in self._store.leases(active=False):
            if lease["project"] == old and new != old:
                self._update(lease["lease_id"], project=new)
            elif lease["project"] == old and not lease.get("ended"):
                self._changed(lease["lease_id"], self.shown(lease))
        self._policy = None  # the projects that can be lent are read again

    def send(self, thread_id: str, text: str, client_msg_id: str | None = None,
             attachments: Sequence[Attachment] = (), aside: str = "") -> dict[str, Any]:
        """What the user said goes to the session as it is, with the files; `aside`: a note the session is told
        with it (what Muninn took off its hands), never shown as the user's words."""
        lease = self.lease_for(thread_id)
        if lease is None:
            raise ValueError(self.words("lease.not_lent"))
        if lease["state"] == "terminal":
            raise ValueError(self.words("lease.in_terminal"))
        if lease["state"] in ("detaching", "detached"):
            raise ValueError(self.words("lease.taking_back"))
        now = self._clock.now()
        sent = self.words("lease.sent_files", names=self.words.join(a.name for a in attachments)) if attachments else ""
        text = text or sent  # only files: the session is told what came
        unsaid = self._take_relays(lease["lease_id"])  # what Muninn had yet to tell it: before, as it came before
        said = "\n\n".join(x for x in (relayed(unsaid, alone=False, words=self.words) if unsaid else "", text, aside) if x)
        try:
            self._runner.send(lease["lease_id"], said, client_msg_id or secrets.token_hex(8), attachments)
        except RunnerError as e:
            self._hold(lease["lease_id"], unsaid, first=True)
            if e.category == "not_found":
                self.forget(lease["lease_id"])
                raise ValueError(self.words("lease.talk_gone")) from e
            raise ValueError(friendly(e, words=self.words)) from e
        # what they said as they said it, and the files themselves (shown again), not a line naming them
        self._store.add_lease_turn(lease["lease_id"], "user", "" if text == sent and attachments else text, now,
                                   [a.meta for a in attachments])
        self._heard(lease["lease_id"], unsaid, now)  # after the user's line, as the open screen shows it
        self._update(lease["lease_id"], last_user_at=now.isoformat(), last_activity=now.isoformat(),
                                 warn_state="none", answering=1)
        return {"leased": True, "lease_id": lease["lease_id"], "thread": thread_id}

    # ── what Muninn tells a talk of its answer beside it ────────────────────

    def relay(self, lease_id: str, text: str) -> bool:
        """Tells the talk the one sentence of what Muninn answered beside it that is the project's, as a message of
        its own (a new id each time): at once, else once the session has answered what it is at (`flush_relays`),
        or before the user's next words there when it could not be sent (`send`). Only when the user sends everything
        to the cloud (it was written from the user's other things too); a talk being given back or in a terminal is not
        told, and the sentence stays in Muninn's memory of the talk (`_finish`). Whether it was told now. Calls the
        runner: never under the engine's lock."""
        text = text.strip()
        lease = self._store.lease(lease_id)
        if not self._share_uncleared or not text or lease is None or lease.get("ended"):
            return False
        self._hold(lease_id, [text])
        lease = self._store.lease(lease_id) or lease
        return not lease.get("answering") and self.flush_relays(lease_id)

    def flush_relays(self, lease_id: str) -> bool:
        """What Muninn has yet to tell a talk, in one message (the session answers it as a turn of its own), unless
        the talk is being given back, is over or in a terminal. Whether it was told. Calls the runner."""
        lease = self._store.lease(lease_id)
        if lease is None or lease.get("ended") or lease["state"] in ("detaching", "detached", "terminal"):
            return False
        unsaid = self._take_relays(lease_id)  # taken: told once, however many try at the same time
        if not unsaid:
            return False
        try:
            self._runner.send(lease_id, relayed(unsaid, alone=True, words=self.words), secrets.token_hex(8))
        except RunnerError as e:
            self._hold(lease_id, unsaid, first=True)
            if e.category == "not_found":
                self.forget(lease_id)
            return False
        now = self._clock.now()
        self._heard(lease_id, unsaid, now)
        self._update(lease_id, answering=1, last_activity=now.isoformat())  # not the user speaking: last_user_at stays
        return True

    def _heard(self, lease_id: str, said: Sequence[str], now: datetime) -> None:
        """What the talk was told of Muninn's answers, kept among its lines and shown once it has heard it."""
        for x in said:
            self._store.add_lease_turn(lease_id, "relay", x, now)
            self._told(lease_id, {"type": "relay", "text": x})

    def _hold(self, lease_id: str, said: Sequence[str], first: bool = False) -> None:
        if not said:
            return
        with self._relay_guard:
            held = self._store.get_state(RELAY) or {}
            mine = list(held.get(lease_id) or [])
            self._store.set_state(RELAY, {**held, lease_id: [*said, *mine] if first else [*mine, *said]})

    def _take_relays(self, lease_id: str) -> list[str]:
        with self._relay_guard:
            held = self._store.get_state(RELAY) or {}
            mine = held.pop(lease_id, None)
            if mine is not None:
                self._store.set_state(RELAY, held)
        return list(mine or [])

    @contextmanager
    def working_beside(self, lease_id: str) -> Iterator[None]:
        """Muninn is doing its part of something said in this talk: until it is done, the talk is not given back for
        the user being done with it or having moved on (`return_idle`), so it can still be told what is its."""
        with self._relay_guard:
            self._beside[lease_id] = self._beside.get(lease_id, 0) + 1
        try:
            yield
        finally:
            with self._relay_guard:
                left = self._beside.pop(lease_id, 1) - 1
                if left:
                    self._beside[lease_id] = left

    def _at_work(self, lease_id: str) -> bool:
        with self._relay_guard:
            return bool(self._beside.get(lease_id))

    def _unsaid(self, lease_id: str) -> bool:
        with self._relay_guard:
            return bool((self._store.get_state(RELAY) or {}).get(lease_id))

    def keepalive(self, thread_id: str) -> dict[str, Any]:
        lease = self._need(thread_id)
        try:
            self._runner.keepalive(lease["lease_id"])
        except RunnerError as e:
            raise ValueError(friendly(e, words=self.words)) from e
        self._update(lease["lease_id"], warn_state="none", last_user_at=self._clock.now().isoformat())
        return {"ok": True}

    def terminal(self, thread_id: str) -> dict[str, Any]:
        lease = self._need(thread_id)
        try:
            got = self._runner.terminal(lease["lease_id"])
        except RunnerError as e:
            raise ValueError(friendly(e, words=self.words)) from e
        self._update(lease["lease_id"], state="terminal")
        return {"command": got["command"], "hint": got.get("hint") or "", "lease_id": lease["lease_id"]}

    def detach(self, thread_id: str, reason: str = "user") -> dict[str, Any]:
        lease = self._need(thread_id)
        try:
            self._runner.detach(lease["lease_id"], reason)
        except RunnerError as e:
            if e.category == "not_found":  # the runner already forgot it: it is over here as well
                self.forget(lease["lease_id"])
                return {"lease_id": lease["lease_id"], "state": "detached"}
            raise ValueError(friendly(e, words=self.words)) from e
        self._update(lease["lease_id"], state="detaching", end_reason=reason)
        return {"lease_id": lease["lease_id"], "state": "detaching"}

    def set_mode(self, thread_id: str, mode: str) -> dict[str, Any]:
        """The user allowed the session to work (writes and commands, each asked first), or took it back to read-only."""
        if mode not in MODE_NOTES:
            raise ValueError(self.words("lease.which_mode"))
        lease = self._need(thread_id)
        if lease["mode"] == mode:
            return {"lease_id": lease["lease_id"], "mode": mode}
        if mode == "work" and not self.can_work(lease["project"]):
            raise ValueError(self.words("lease.work_not_allowed"))
        try:
            self._runner.set_mode(lease["lease_id"], mode)
        except RunnerError as e:
            if e.category == "busy":
                raise ValueError(self.words("lease.mode_busy")) from e
            raise ValueError(friendly(e, words=self.words)) from e
        now = self._clock.now()
        self._update(lease["lease_id"], mode=mode, last_activity=now.isoformat())
        note = self.words(MODE_NOTES[mode])
        self._store.add_lease_turn(lease["lease_id"], "answer", note, now)
        return {"lease_id": lease["lease_id"], "mode": mode, "note": note}

    def forget(self, lease_id: str) -> None:
        """The runner no longer knows this lease (it lost it): end it here and give the thread back, so the user
        is never locked out of their conversation."""
        lease = self._store.lease(lease_id)
        if lease is None or lease.get("ended"):
            return
        self._update(lease_id, state="detached", error="not_found")
        self._finish(self._store.lease(lease_id) or lease, self._clock.now(), lost=True)

    def view(self, thread_id: str) -> dict[str, Any] | None:
        """The lease of a thread with its turns, for the conversation view."""
        lease = self.lease_for(thread_id)
        if lease is None:
            return None
        return {**self.shown(lease), "can_work": lease["project"] in self._listed("work_projects"),
                "turns": self._store.lease_turns(lease["lease_id"]), "quota_warning": self.quota_warning()}

    def active(self) -> list[dict[str, Any]]:
        """The lent conversations, for a way back to each from the home page."""
        return [{"lease_id": x["lease_id"], "project": x["project"], "label": self.label(x["project"]), "thread": x["thread"], "state": x["state"],
                 "warn": x["warn_state"] in IDLE} for x in self._store.leases()]

    def transcript(self, lease_id: str) -> dict[str, Any] | None:
        """A lease's conversation and its handover, to reread after it came back."""
        lease = self._store.lease(lease_id)
        if lease is None:
            return None
        return {"lease_id": lease_id, "project": lease["project"], "label": self.label(lease["project"]), "ended": lease.get("ended"),
                "turns": self._store.lease_turns(lease_id), "handover": lease.get("handover")}

    def thread_of(self, lease_id: str) -> str | None:
        lease = self._store.lease(lease_id)
        return lease.get("thread") if lease else None

    def _need(self, thread_id: str) -> dict[str, Any]:
        lease = self.lease_for(thread_id)
        if lease is None:
            raise ValueError(self.words("lease.not_lent"))
        return lease

    # ── the runner's side: its events ───────────────────────────────────────

    def on_event(self, ev: dict[str, Any]) -> bool:
        """Applies one event; False for one already seen (events can be replayed after a reconnect)."""
        lease = self._store.lease(str(ev.get("lease_id")))
        seq = int(ev.get("seq") or 0)
        if lease is None or seq <= int(lease["last_seq"] or 0):
            return False
        now = self._clock.now()
        kind = ev.get("type")
        fields: dict[str, Any] = {"last_seq": seq}
        if kind in WORKING:
            fields["last_activity"] = now.isoformat()
        if kind == "state":
            state = str(ev.get("state") or "")
            if state:
                fields["state"] = state
            if ev.get("mode") in MODE_NOTES:
                fields["mode"] = ev["mode"]
            if isinstance(ev.get("project"), str) and ev["project"]:  # it goes by a new name (its sandbox made a project)
                fields["project"] = ev["project"]
            if ev.get("resumed") is False and ev.get("detail"):  # it was to go on from last time and could not
                self._store.add_lease_turn(lease["lease_id"], "answer",
                                           self.words("lease.not_resumed", detail=ev["detail"]), now)
        elif kind == "assistant":
            text = str(ev.get("text") or "")
            if text:
                self._store.add_lease_turn(lease["lease_id"], "session", text, now)
            fields["last_activity"] = now.isoformat()
        elif kind == "turn_end":
            fields["answering"] = 0
        elif kind == "file" and isinstance(ev.get("id"), str):  # a file of the project the session showed the user
            self._store.add_lease_turn(lease["lease_id"], "file", str(ev.get("summary") or ""), now, files=[{
                "id": ev["id"], "name": str(ev.get("name") or self.words("lease.a_file")), "media_type": str(ev.get("mime") or ""),
                "size": int(ev.get("size") or 0), "path": str(ev.get("path") or ""), "lease": lease["lease_id"]}])
        elif kind == "quota":  # the session's usage windows: {id, label, used (0..1), resets_at (ISO)}
            windows = {str(w["id"]): {"utilization": float(w["used"]), "resetsAt": _stamp(w.get("resets_at")), "label": w.get("label"),
                                      "at": now.timestamp()}
                       for w in ev.get("windows") or [] if isinstance(w, dict) and w.get("id") and isinstance(w.get("used"), (int, float))}
            if windows:
                self._store.set_state("lease:rate_limit", {**(self._store.get_state("lease:rate_limit") or {}), **windows})
        elif kind == "idle_warning":
            if lease["warn_state"] in ("none", "answered"):
                fields["warn_state"] = "pending"
        elif kind == "attachments":  # where the session kept the files the user sent: said beside the talk
            paths = [str(x) for x in ev.get("paths") or []]
            if paths:
                self._store.add_lease_turn(lease["lease_id"], "answer", self.words("lease.saved_files", project=self.label(lease["project"]),
                                                                                  n=len(paths)), now,
                                           detail="\n".join(paths))
        elif kind == "handover":
            fields["handover"] = ev.get("handover") if isinstance(ev.get("handover"), dict) else None
        elif kind == "error":
            fields["error"] = str(ev.get("error") or "internal")
            fields["answering"] = 0  # the turn is over, one way or another
        lease = self._update(lease["lease_id"], **fields) or lease
        if lease["state"] == "detached" and not lease.get("ended"):
            self._finish(lease, now)
        return True

    def _finish(self, lease: dict[str, Any], now: datetime, lost: bool = False) -> None:
        """The thread comes back to Muninn, once however many ways the talk was seen to end: the lease reference goes,
        the handover summary joins it (after what Muninn said beside the talk, a turn of its own: Muninn remembers
        it), the conversation is open again (nothing asked; where it got to is in the talk), and what follows from
        the talk having ended is done."""
        def claim() -> bool:
            if (self._store.lease(lease["lease_id"]) or {}).get("ended"):
                return False
            self._update(lease["lease_id"], ended=now.isoformat(), warn_state="none")
            return True

        if not self._serialize(claim):
            return
        called = self.label(lease["project"])
        text = (self.words("lease.talk_lost", project=called) if lost
                else handover_text(called, lease.get("handover"), words=self.words))
        # what Muninn said beside it since it was last gone into, and what it had yet to tell it: Muninn's to remember
        since = str(lease.get("started") or "")
        beside = beside_memory(called, [x for x in self._store.lease_turns(lease["lease_id"])
                                        if x["role"] in ("muninn", "relay") and str(x.get("ts") or "") >= since],
                               self._take_relays(lease["lease_id"]), words=self.words)

        def back(t: dict[str, Any]) -> None:
            if t.get("lease") == lease["lease_id"]:
                t.pop("lease", None)
            card = {} if lost else {"handover": handover_card(called, lease.get("handover"))}
            if not lost:  # what it leaves undecided is the latest handover's word: none listed, none left
                left = card["handover"]["open_questions"]
                if left:
                    t["talk_left"] = {"lease": lease["lease_id"], "project": lease["project"], "called": called,
                                      "questions": left, "ts": now.isoformat()}
                else:
                    t.pop("talk_left", None)
            # kept apart from the handover, whose text is read as the talk's report
            kept = [{"role": "muninn", "text": beside, "ts": now.isoformat(), "local_only": True,
                     "via": [f"lease:{lease['lease_id']}"], "beside": lease["lease_id"]}] if beside else []
            t["turns"] = list(t.get("turns") or []) + kept + [
                {"role": "muninn", "text": text, "ts": now.isoformat(), "local_only": True, "via": f"lease:{lease['lease_id']}",
                 "ended": lease["lease_id"], **card}]
            t["ts"] = now.isoformat()
            if t.get("topic"):  # no one is at it now; where it got to is in the talk
                t["topic"] = {**t["topic"], "state": "open", "now": self.words("lease.talked_until", project=called, at=now),
                              "ts": now.isoformat()}

        self._update_thread(lease["thread"], back)
        self._serialize(lambda: self._ended(lease, None if lost else text))

    def left(self, thread_id: str) -> None:
        """The user just spoke to Muninn in conversation `thread_id`: every conversation lent elsewhere was left."""
        now = self._clock.now().isoformat()
        for lease in self._store.leases():
            if lease["thread"] != thread_id and lease["state"] not in ("detaching", "detached", "terminal"):
                self._update(lease["lease_id"], left_at=now)

    def end_when_done(self, thread_id: str) -> None:
        """The user said they are done with the talk for now (「今天先這樣,收回」): once the session has answered that
        (it wraps up), it is given back, and the conversation goes on with Muninn."""
        lease = self.lease_for(thread_id)
        if lease is not None and lease["state"] not in ("detaching", "detached", "terminal"):
            self._update(lease["lease_id"], end_asked=self._clock.now().isoformat())

    def return_idle(self) -> int:
        """Gives back, without asking and in the background, every lent conversation that is done with: one the
        user said they are done with (once the session has answered that), one the runner found idle, and one the
        user left for another conversation (they spoke to Muninn elsewhere since their last word here: `left`). A session still at what it was asked (an experiment running, an answer being
        written) finishes first and comes back after its turn ends; for the last two, so does Muninn's part of what
        was said there, and what it has to tell the session (`relay`). The session writes its handover, which joins
        the conversation. Calls the runner: never under the engine's lock."""
        returned = 0
        for lease in self._store.leases():
            if lease["state"] in ("detaching", "detached", "terminal"):
                continue
            said_here = lease.get("last_user_at") or lease.get("started")
            left = lease.get("left_at")
            leaving = bool(lease.get("end_asked")) or bool(left and said_here and left > said_here)
            # what Muninn has for it goes first: it comes back once Muninn is done and the session has answered that
            at_work, unsaid = self._at_work(lease["lease_id"]), self._unsaid(lease["lease_id"])
            if leaving and unsaid and not at_work and not lease.get("answering") and lease["warn_state"] not in IDLE:
                self.flush_relays(lease["lease_id"])  # it could not be told before: tried again, given back after
                continue
            waits = bool(lease.get("answering")) or at_work or unsaid
            moved_on = bool(left and said_here and left > said_here and not waits)
            done = bool(lease.get("end_asked") and not waits)
            if lease["warn_state"] not in IDLE and not moved_on and not done:
                continue
            reason = "user" if done else "idle" if lease["warn_state"] in IDLE else "moved_on"
            try:
                self._runner.detach(lease["lease_id"], SAID_AS.get(reason, reason))
            except RunnerError as e:
                if e.category == "not_found":
                    self.forget(lease["lease_id"])
                continue
            self._update(lease["lease_id"], state="detaching", end_reason=reason, warn_state="none")
            returned += 1
        return returned


OPENING_TURNS = 12
# what describes one stretch of a lease, from being lent to being given back: cleared when the same lease is lent
# again, so nothing of how it ended (the user asking to end it, a warning, leaving it) carries into the next stretch.
# (tests/test_lease.py keeps every column of a lease either here, set afresh on lending, or the lease's own for good.)
STRETCH = {"ended": None, "handover": None, "end_reason": None, "error": None, "warn_state": "none", "warned_at": None,
           "answering": 0, "left_at": None, "end_asked": None}


def entering(thread: dict[str, Any] | None, since: str | None = None, *, uncleared: bool = False) -> str | None:
    """What the user said to go into a talk (their last words here, after `since`): what it is told when nothing
    more of the conversation may or need go to it, so they never say it twice. As everything handed over, only
    when it may go there (cleared, or the user sends everything to the cloud: `uncleared`)."""
    last = next((t for t in reversed((thread or {}).get("turns") or []) if t.get("role") == "user"
                 and (since is None or str(t.get("ts") or "") > since)), None)
    if not last or not last.get("text") or not (uncleared or last.get("cloud_ok")):
        return None
    return str(last["text"])


def opening(thread: dict[str, Any] | None, project: str, lent_from: Callable[[dict[str, Any]], set[str]], *,
            uncleared: bool = False, since: str | None = None, words: Language) -> str | None:
    """What a talk is told of this conversation when it is gone into: what was said here since it last heard it
    (`since`: it remembers the talk before), or, for an idea's sandbox opened now, what was said about the idea.
    Only when every sentence handed over may go there: what a lent session said (marked local_only) only to the
    projects it came from (`lent_from`: theirs, empty when unknown), and what was not cleared only when the user
    sends everything to the cloud (`uncleared`)."""
    thread = thread or {}
    if since is None and (thread.get("about") or {}).get("kind") != "idea":
        return None  # a talk opened from a conversation about something else hears what went into it (`entering`)
    turns = [t for t in thread.get("turns") or [] if (since is None or str(t.get("ts") or "") > since)
             and not t.get("note") and not (t.get("local_only") and lent_from(t) == {project})][-OPENING_TURNS:]  # not its own
    if not turns or any(t.get("local_only") and lent_from(t) != {project} for t in turns) \
            or not (uncleared or all(t.get("cloud_ok") for t in thread["turns"] if t.get("role") == "user")):
        return None
    said = "\n".join(words("lease.said_line", who=words("lease.said_by_user") if t["role"] == "user" else "Muninn", text=t["text"])
                     for t in turns)
    return words("lease.back_since" if since is not None else "lease.take_over", said=said)


def _stamp(iso: Any) -> float | None:
    """A time the runner gives (ISO 8601), as seconds; None when it gave none or nothing readable."""
    try:
        return datetime.fromisoformat(str(iso)).timestamp() if iso else None
    except ValueError:
        return None


TALK_LINES = 8  # a talk's latest lines (a few rounds, Muninn's among them) read beside it to tell what the user's words refer to
TALK_LINE_MAX = 600


def talk_lines(turns: Sequence[dict[str, Any]], project: str) -> list[str]:
    """The latest lines of a lent talk, the user's, the session's and Muninn's (what it said beside the talk, and what it
    told the session), so a sentence said in it can be understood ("this question" is what the session just said).
    Read beside the talk, never kept in Muninn's conversation. Only a model reads these lines, and its prompt names
    their labels (commands.py), so they are written as the prompt is."""
    who = {"user": "the user", "session": project, "muninn": "Muninn", "relay": f"Muninn told {project}"}

    def line(t: dict[str, Any]) -> str:
        if t["role"] == "file":  # what it showed the user: named, never read
            return f"{project}:(showed the user {', '.join(f['name'] for f in t.get('files') or []) or 'a file'})"
        return f"{who[t['role']]}:{str(t['text'])[:TALK_LINE_MAX]}"

    said = [t for t in turns if t.get("role") == "file" or (t.get("role") in who and str(t.get("text") or "").strip())]
    return [line(t) for t in said[-TALK_LINES:]]


def relayed(said: Sequence[str], *, alone: bool, words: Language) -> str:
    """What a talk is told of Muninn's answers beside it; `alone`: a message of its own (not before the user's words),
    which needs no answer."""
    return words("lease.relayed_alone" if alone else "lease.relayed_before", heard=words("lease.heard", said=list(said)))


def beside_memory(project: str, lines: Sequence[dict[str, Any]], unsaid: Sequence[str], *, words: Language) -> str | None:
    """What Muninn said beside a talk (`lines`: its own and what it told the session) and had yet to tell it, as
    Muninn's conversation keeps it once the talk is over; None when it said nothing there."""
    said = [words("lease.told", project=project, text=x["text"]) if x["role"] == "relay" else str(x["text"]) for x in lines]
    said += [words("lease.not_yet_told", project=project, text=x) for x in unsaid]
    if not said:
        return None
    return words("lease.said_beside", project=project, lines="\n".join("- " + x.replace("\n", " ") for x in said))


def lent_text(project: str, work: bool, resumed: bool, *, words: Language) -> str:
    """The line that says, whatever the reply said, that the conversation was handed over and where what the user
    says next goes."""
    return words("lease.lent_resumed" if resumed else "lease.lent_work" if work else "lease.lent_discuss", project=project)


def left_by(left: dict[str, Any] | None, project: str) -> list[str]:
    """The questions a conversation's last deep talk with `project` left undecided."""
    return list((left or {}).get("questions") or []) if (left or {}).get("project") == project else []


def handover_card(project: str, handover: dict[str, Any] | None) -> dict[str, Any]:
    """What a talk's handover says, for the conversation to draw (the text beside it is what a model reads): its
    summary, what was decided (and why), what is still open, and what is the user's to do."""
    h = handover or {}
    return {"project": project, "fallback": bool(h.get("fallback")) or not handover,
            "summary": str(h.get("summary") or "").strip(),
            "decisions": [{"what": str(d["what"]), **({"why": str(d["why"])} if d.get("why") else {})}
                          for d in h.get("decisions") or [] if isinstance(d, dict) and d.get("what")],
            "open_questions": [str(q) for q in h.get("open_questions") or [] if isinstance(q, str) and q.strip()],
            "tasks_for_owner": [{"text": str(x["text"]), **({"due": str(x["due"])} if x.get("due") else {})}
                                for x in h.get("tasks_for_owner") or [] if isinstance(x, dict) and x.get("text")]}


def card_from_text(project: str, text: str) -> dict[str, Any] | None:
    """A handover's card read back from the text it was said in (`handover_text`), for a turn kept before cards
    were; None for a text that is not one. What was decided comes back without its reasons."""
    # Chinese on purpose: the turns kept before cards were written when Muninn spoke only Traditional Chinese, and
    # every turn since keeps its card, so there is no other language's text to read back.
    lines = text.split("\n")
    if not lines or not lines[0].startswith(f"跟 {project} 的深談結束了"):
        return None
    if "交接沒有成功產生" in lines[0] or "沒有交接內容" in lines[0]:
        return handover_card(project, None)
    h: dict[str, Any] = {"summary": "", "decisions": [], "open_questions": [], "tasks_for_owner": []}
    for line in lines[1:]:
        for head, key in (("決定:", "decisions"), ("還沒決定:", "open_questions"), ("給你的待辦:", "tasks_for_owner")):
            if line.startswith(head):
                items = [x.strip() for x in line[len(head):].split(";") if x.strip()]
                h[key] = [{"what": x} for x in items] if key == "decisions" else [{"text": x} for x in items] \
                    if key == "tasks_for_owner" else items
                break
        else:
            h["summary"] = "\n".join(x for x in (h["summary"], line.strip()) if x)
    return handover_card(project, h)


def handover_text(project: str, handover: dict[str, Any] | None, *, words: Language) -> str:
    """A talk's handover as it joins the conversation (and is read there by a model); the screens draw its card."""
    if not handover:
        return words("lease.handover_none", project=project)
    if handover.get("fallback"):
        return words("lease.handover_failed", project=project)
    parts = [words("lease.handover_over", project=project), str(handover.get("summary") or "").strip()]
    decisions = [d.get("what") for d in handover.get("decisions") or [] if isinstance(d, dict) and d.get("what")]
    if decisions:
        parts.append(words("lease.handover_decided", items=words("lease.listed", items=decisions)))
    questions = [q for q in handover.get("open_questions") or [] if isinstance(q, str)]
    if questions:
        parts.append(words("lease.handover_open", items=words("lease.listed", items=questions)))
    tasks = [x.get("text") for x in handover.get("tasks_for_owner") or [] if isinstance(x, dict) and x.get("text")]
    if tasks:
        parts.append(words("lease.handover_tasks", items=words("lease.listed", items=tasks)))
    return "\n".join(p for p in parts if p)


# ── permission requests from a lent session's relay ─────────────────────────

LONG_QUERY = 80
TOKEN = re.compile(r"[A-Za-z0-9+=_%-]{32,}")  # one path segment or query value, never across a "/"
UUID = re.compile(r"[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
HASH = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")  # git commit ids in ordinary links


def _encoded(tok: str) -> bool:
    """Looks like data rather than a name: letters and digits mixed, not a slug of words, an id or a commit."""
    if UUID.fullmatch(tok) or HASH.fullmatch(tok):
        return False
    digits = sum(c.isdigit() for c in tok)
    letters = sum(c.isalpha() for c in tok)
    return digits >= 4 and letters >= 4 and tok.count("-") <= len(tok) // 10


def suspicious_url(url: str) -> bool:
    """A URL that carries a lot of data: a long query, or a long encoded-looking piece in one path segment or
    query value. That is what sending something out through a fetch looks like."""
    try:
        u = urlsplit(url)
    except ValueError:
        return True
    tail = u.query + ("#" + u.fragment if u.fragment else "")
    if len(tail) > LONG_QUERY:
        return True
    pieces = u.path.split("/") + re.split(r"[&=#]", tail)
    return any(_encoded(m.group()) for piece in pieces for m in TOKEN.finditer(piece))


def domain_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def in_domains(host: str, suffixes: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == s.lower() or host.endswith("." + s.lower().lstrip(".")) for s in suffixes)


class PermissionDesk:
    def __init__(self, store: PermissionStorage, clock: Clock, channel: Channel, quiet: Callable[[datetime], bool],
                 leases: ChannelLease, *, push_titles: bool = True,
                 trust: CommandTrust | None = None, words: Language):
        """`trust`: which commands may run without asking (kinds the user allowed there before, reaching nothing past
        the project); none: every command is asked."""
        self.words = words
        self._store = store
        self._trust = trust
        self._clock = clock
        self._channel = channel
        self._quiet = quiet
        self._leases = leases
        self._titles = push_titles

    def receive(self, req: dict[str, Any]) -> dict[str, Any]:
        """A request from the relay, stored once by its id; the user is told about a new one."""
        rid, what = req.get("id"), req.get("kind")
        if not isinstance(rid, str) or not rid or what not in KINDS:
            raise ValueError(f"id and kind ({', '.join(KINDS)}) are required")
        if req.get("v", 1) not in (1, 2):
            raise ValueError(f"unsupported version {req.get('v')!r}")
        now = self._clock.now()
        before = self._store.permission_request(rid)
        row = self._store.add_permission_request(req, now)
        if before is None and kind(row) == "run_command" and self._trust is not None \
                and self._trust.allows(row.get("project"), str(row.get("input_summary") or "")):
            decided = self._store.decide_permission(rid, "allow", None, "auto", now)
            if decided is not None:  # allowed as it was before; said in the talk, nobody is asked
                return {**decided, "note": self._note(decided, now)}
        if before is None:
            self._tell(row, now)
        return row

    def _tell(self, row: dict[str, Any], now: datetime) -> None:
        lease = self._store.lease(row["lease_id"]) if row.get("lease_id") else None
        talking = bool(lease and lease.get("last_user_at")
                       and now - datetime.fromisoformat(lease["last_user_at"]) <= ACTIVE_TALK)
        if self._quiet(now) and not talking:
            return  # it waits on the home page; unanswered, it is denied after five minutes
        project = self._leases.label(row["project"]) if row.get("project") else self.words("lease.a_project")
        title = self.words(PUSH_TITLES.get(kind(row), "lease.push_other"), project=project)
        self._channel.send([PushMessage("muninn", 1, title=title if self._titles else None)])

    def auto_commands(self, on: bool | None = None) -> bool | None:
        """Whether commands of a kind the user allowed before run without asking (`on`: turned so first); None when
        every command is asked."""
        if self._trust is None:
            return None
        return self._trust.turn(on) if on is not None else self._trust.on()

    def result(self, rid: str) -> dict[str, Any] | None:
        """The decision for the relay, or None while it is still open."""
        self.expire()
        row = self._store.permission_request(rid)
        if row is None:
            raise KeyError(rid)
        if row["decision"] is None:
            return None
        out: dict[str, Any] = {"v": int(row.get("v") or 1), "decision": row["decision"]}  # in the version it was asked in
        if row.get("answers"):
            out["answers"] = row["answers"]
        return out

    def pending(self) -> list[dict[str, Any]]:
        """Open requests, shaped for the cards."""
        self.expire()
        no_session = self._no_session()
        return [self._card(row, no_session) for row in self._store.permission_requests()]

    def card(self, rid: str) -> dict[str, Any] | None:
        """One open request, shaped like the cards (None once it is decided)."""
        row = self._store.permission_request(rid)
        return self._card(row, self._no_session()) if row and row["decision"] is None else None

    def _no_session(self) -> list[str] | None:
        try:
            return list(self._leases.policy().get("no_session_domains") or [])
        except RunnerError:
            return None  # unknown: never offer "allow this site for the whole conversation"

    def _card(self, row: dict[str, Any], no_session: list[str] | None) -> dict[str, Any]:
        card = {"id": row["id"], "lease": row.get("lease_id"), "project": row.get("project"),
                **({"label": self._leases.label(row["project"])} if row.get("project") else {}), "kind": kind(row),
                "created": row["created"], "question": row.get("question")}
        if kind(row) in WORK_RISKS:
            card.update(summary=row.get("input_summary"), allow_session=kind(row) == "write_file")
        elif kind(row) == "open_web":
            url = str(row.get("input_summary") or "")
            host = domain_of(url)
            card.update(url=url, domain=host, flagged=suspicious_url(url),
                        allow_session=no_session is not None and bool(host) and not in_domains(host, no_session))
        else:
            card["summary"] = row.get("summary") or row.get("input_summary")
        return card

    def decide(self, rid: str, decision: str, answers: dict[str, Any] | None = None) -> dict[str, Any]:
        """`answers`: for a question, {question: the label chosen}, or the labels chosen where it takes several."""
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
        row = self._store.permission_request(rid)
        if row is None:
            raise KeyError(rid)
        if row["decision"] is not None:
            raise ValueError(self.words("lease.already_decided"))
        if decision == "allow_session" and kind(row) == "run_command":
            raise ValueError(self.words("lease.commands_each"))
        if decision == "allow_session" and kind(row) != "write_file":
            if kind(row) != "open_web":
                raise ValueError(self.words("lease.session_web_files"))
            card = next((c for c in self.pending() if c["id"] == rid), None)
            if card is None or not card.get("allow_session"):
                raise ValueError(self.words("lease.open_site"))
        if kind(row) == "ask_user" and decision == "allow" and not answers:
            raise ValueError(self.words("lease.pick_option"))
        now = self._clock.now()
        decided = self._store.decide_permission(rid, decision, answers, "user", now)
        if decided is None:
            return row
        return {**decided, "note": self._note(decided, now)}

    def expire(self) -> int:
        """Requests nobody answered in five minutes are denied."""
        now = self._clock.now()
        n = 0
        for row in self._store.permission_requests():
            if now - datetime.fromisoformat(row["created"]) >= REQUEST_TIMEOUT:
                if decided := self._store.decide_permission(row["id"], "deny", None, "timeout", now):
                    self._note(decided, now, timed_out=True)
                n += 1
        return n

    def _note(self, row: dict[str, Any], now: datetime, timed_out: bool = False) -> str:
        """What the user decided joins the lent conversation, so it reads back when they return to it."""
        text = answer_text(row, timed_out, words=self.words)
        if row.get("lease_id"):
            self._store.add_lease_turn(row["lease_id"], "answer", text, now)
        return text


WORK_RISKS = ("write_file", "run_command")  # what a session in work mode asks before doing
KINDS = ("ask_user", "read", "search", "write_file", "run_command", "open_web", "other")  # what a session's step or request is


def kind(row: dict[str, Any]) -> str:
    """What a permission request is about: a question for the user, a web page to open, (work mode) a file to
    write or a command to run, or something else."""
    return str(row.get("kind") or "other")


WRITE_ANSWERS = {"allow": "lease.write_allowed", "allow_session": "lease.write_allowed_session", "deny": "lease.write_denied"}
RUN_ANSWERS = {"allow": "lease.run_allowed", "deny": "lease.run_denied"}
OTHER_ANSWERS = {"allow": "lease.other_allowed", "deny": "lease.other_denied"}
WEB_ANSWERS = {"allow": "lease.web_allowed", "allow_session": "lease.web_allowed_session", "deny": "lease.web_denied"}


def answer_text(row: dict[str, Any], timed_out: bool = False, *, words: Language) -> str:
    """One line saying what became of a permission request."""
    decision = row["decision"]
    if kind(row) == "ask_user":
        if timed_out or decision == "deny":
            return words("lease.ask_unanswered") if timed_out else words("lease.ask_declined")
        answers = row.get("answers") or {}
        said = {q: words.join(a) if isinstance(a, list) else str(a) for q, a in answers.items()}
        if len(said) == 1:
            return words("lease.ask_chose", answer=next(iter(said.values())))
        return words("lease.ask_chose", answer=words("lease.listed", items=[
            words("lease.ask_pair", question=q, answer=a) for q, a in said.items()]))
    if kind(row) in WORK_RISKS:
        what = str(row.get("input_summary") or "")
        short = what if len(what) <= 80 else what[:79] + "…"
        if kind(row) == "write_file":
            if timed_out:
                return words("lease.write_timeout", what=short)
            return words(WRITE_ANSWERS[decision], what=short) if decision in WRITE_ANSWERS else decision
        if timed_out:
            return words("lease.run_timeout", what=short)
        if row.get("by") == "auto":
            return words("lease.run_auto", what=short)
        return words(RUN_ANSWERS[decision], what=short) if decision in RUN_ANSWERS else decision
    if kind(row) != "open_web":
        what = str(row.get("input_summary") or words("lease.this_thing"))
        short = what if len(what) <= 80 else what[:79] + "…"
        if timed_out:
            return words("lease.other_timeout", what=short)
        return words(OTHER_ANSWERS[decision], what=short) if decision in OTHER_ANSWERS else decision
    host = domain_of(str(row.get("input_summary") or "")) or words("lease.this_page")
    if timed_out:
        return words("lease.web_timeout", host=host)
    return words(WEB_ANSWERS[decision], host=host) if decision in WEB_ANSWERS else decision
