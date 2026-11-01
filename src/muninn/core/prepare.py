"""Getting things ready so the user only has to review and act.

An application packet: the public posting, the best of the user's resume
versions, draft answers to the usual form questions, and what to ask on the
first call. Muninn sends nothing on its own: an application form it fills (core/apply.py) goes out on the user's yes."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Callable, Protocol, Sequence

from .actions import ActionGate, DecidedBy, PacketSetAside, PacketSubmitted, PreparePacket, Ran
from .decide import Decider, Question
from .llm_router import LLMRequest, LLMRouter
from .models import ActionRecord, DomainPlan, InboxItem, Posting, Prepared, TrackerItem
from .ports import ActionLog, Clock, CompletionStore, DocumentSource, MarkerStore, PostingFetcher, PreparedStore, StateStore
from .progress import deadline_statuses
from .timewin import local_date
from .privacy import packet_payload
from .tools import Tool
from .tools.packets import DismissPacket, PacketSent
from .language import Language, written_in
from .reply_json import json_object

log = logging.getLogger(__name__)

PACKET = "application_packet"

PACKET_SYSTEM = """You prepare a job application packet that the user will review and submit themselves.
Input is JSON: the target role, the tracker's next_action, answer_language, the public posting, and the user's own documents (resume versions).
Reply with one JSON object and nothing else:
{"summary": "...", "match": ["...", "...", "..."], "gaps": ["..."], "resume": "<exact name of the best document>",
 "resume_focus": "...", "answers": [{"q": "...", "a": "..."}], "ask_first": ["..."]}
Rules:
- summary, match, gaps, resume_focus and ask_first are in {language}. match: up to 3 concrete reasons the documents fit the posting. gaps: requirements the documents do not show.
- answers: draft answers to the posting's form questions if it lists any, otherwise to "Why do you want to join?", "Expected salary" and "Earliest start date". Write them in answer_language, at most 90 words each.
- resume: prefer the document written in the posting's language.
- Expected salary: ask for the role's level and salary band first, with no number.
- Experience, employers, dates and numbers come from the documents.
- The user's employment status, availability, notice period and reasons for changing jobs are bracketed placeholders for the user to fill in, e.g. [start date].
- ask_first: questions for the first recruiter call, including any the next_action asks for.
"""

FIT_SYSTEM = """You check whether a job posting fits the user before an application is prepared for it.
Input is JSON: the target role, the tracker's next_action, the public posting, the user's own documents (resume versions),
and the user's own conditions for a job (filters: e.g. employment type, location, pay; may be empty).
Reply with one JSON object and nothing else: {"verdict": "fit" | "doubt" | "no", "why": "..."}
Rules:
- fit: every basic / minimum / required qualification in the posting is clearly met by the documents, and nothing in the posting goes against the user's conditions.
- A condition the posting clearly breaks (e.g. a contract role when the user only wants full-time) makes it no; one the posting does not say anything about makes it doubt.
- doubt: a required qualification cannot be judged from the documents.
- no: a required qualification is clearly not met (e.g. fewer years than asked, a required kind of experience the documents do not show).
- why: one sentence in {language} naming the specific requirement or condition and what the documents or the posting show; for fit, the strongest reason it fits.
"""
VERDICTS = ("fit", "doubt", "no")


def packet_system(words: Language) -> str:
    return written_in(PACKET_SYSTEM, words)


def fit_system(words: Language) -> str:
    return written_in(FIT_SYSTEM, words)


REQUIRED = ("summary", "match", "answers", "resume")
LANGUAGE_NAMES = {"zh": "Traditional Chinese (Taiwan)", "en": "English"}


def text_language(text: str) -> str:
    """'zh' when CJK characters make up a real share of the letters, else 'en'.
    Decided in code: small local models get this wrong."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return "en"
    cjk = sum(1 for c in letters if "\u4e00" <= c <= "\u9fff")
    return "zh" if cjk / len(letters) > 0.2 else "en"


def matching_documents(posting_text: str, docs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The documents in the posting's language, or all of them if none match."""
    lang = text_language(posting_text)
    same = [d for d in docs if text_language(d[1]) == lang]
    return same or docs


def parse_packet(reply: str | None) -> dict[str, Any] | None:
    data = json_object(reply)
    if data is None or any(k not in data for k in REQUIRED):
        return None
    data["answers"] = [a for a in data.get("answers") or [] if isinstance(a, dict) and a.get("q") and a.get("a")]
    for key in ("match", "gaps", "ask_first"):
        data[key] = [str(x) for x in data.get(key) or []]
    return data


class PacketPreparer:
    def __init__(
        self,
        router: LLMRouter,
        fetcher: PostingFetcher,
        documents: DocumentSource,
        *,
        decider: Decider,
        cloud_drafts: bool,
    ):
        """`decider`: who judges the fit check (packet_fit)."""
        self._router = router
        self._decider = decider
        self._fetcher = fetcher
        self._documents = documents
        self._cloud = cloud_drafts
        self._checked: dict[str, Posting] = {}  # a posting the fit check just read, handed to the draft that follows

    @property
    def cloud_drafts(self) -> bool:
        return self._cloud

    def fetch_posting(self, url: str) -> Posting | None:
        return self._fetcher.fetch(url)

    def documents(self) -> list[tuple[str, str]]:
        """The user's reference documents (resume versions), name and text."""
        return list(self._documents.documents())

    def _material(self, plan: DomainPlan, item: TrackerItem, *, keep: bool = False) -> tuple[Posting, list[tuple[str, str]]] | None:
        """The posting and the matching documents. `keep`: hold the posting for the draft that comes next."""
        target = plan.target(item.target)
        if target is None or not target.url:
            return None
        posting = self._checked.pop(target.url, None) or self._fetcher.fetch(target.url)
        if posting is None:
            log.info("could not read posting for %s", item.target)
            return None
        docs = self._documents.documents()
        if not docs:
            log.info("no reference documents configured; skipping %s", item.target)
            return None
        if keep:
            self._checked[target.url] = posting
        return posting, matching_documents(posting.text, docs)

    def judge_fit(self, plan: DomainPlan, item: TrackerItem, *, words: Language) -> dict[str, Any] | None:
        """Whether the posting's required qualifications are met by the user's documents and the posting
        keeps to the user's own conditions (the plan's filters): {verdict: fit | doubt | no, why, case}; None
        when it could not be judged now. The conditions are the user's own words, so this is not cloud-safe
        material: where it goes is the routing's call (the cloud when the user chose it for everything)."""
        got = self._material(plan, item, keep=True)
        if got is None:
            return None
        posting, docs = got
        title = plan.target_title(item.target)
        verdict = self._decider.decide(Question(
            point="packet_fit",
            system=fit_system(words),
            content=json.dumps(
                {"target": title, "next_action": item.next_action,
                 "posting": {"url": posting.url, "title": posting.title, "location": posting.location, "text": posting.text},
                 "documents": [{"name": n, "text": t} for n, t in docs],
                 "filters": list(plan.filters)}, ensure_ascii=False),
            labels=VERDICTS,
            sensitivity=plan.sensitivity_default,
            domain=plan.id,
            field="verdict",
        ))
        if verdict is None:
            return None
        return {"verdict": verdict.label, "why": verdict.why, "case": verdict.case}

    def prepare(self, plan: DomainPlan, item: TrackerItem, now: datetime, *, words: Language) -> Prepared | None:
        got = self._material(plan, item)
        if got is None:
            return None
        posting, docs = got
        answer_language = LANGUAGE_NAMES[text_language(posting.text)]
        title = plan.target_title(item.target)
        content = packet_payload(plan, title, item.next_action, posting, docs, answer_language) if self._cloud else None
        request = LLMRequest(
            system=packet_system(words),
            # without cloud drafting the same material is sent as sensitive (local model unless routing is cloud)
            content=content if content is not None else json.dumps(
                {"target": title, "next_action": item.next_action, "answer_language": answer_language,
                 "posting": {"url": posting.url, "title": posting.title, "location": posting.location, "text": posting.text},
                 "documents": [{"name": n, "text": t} for n, t in docs]},
                ensure_ascii=False,
            ),
            sensitivity=plan.sensitivity_default,
            domain=plan.id,
            json_mode=True,
            task="packet",
        )
        reply, backend = self._router.complete_with_backend(request)
        packet = parse_packet(reply)
        if packet is None:
            log.warning("packet for %s: no usable reply", item.target)
            return None
        names = [n for n, _ in docs]
        if packet["resume"] not in names:
            packet["resume"] = names[0]
        return Prepared(
            id=None,
            ts=now,
            domain=plan.id,
            target=item.target,
            kind=PACKET,
            title=title,
            data={
                "posting": {"url": posting.url, "title": posting.title, "location": posting.location},
                "packet": packet,
                "next_action": item.next_action,
            },
            prepared_by=backend or "",
        )


class PacketStorage(PreparedStore, ActionLog, CompletionStore, MarkerStore, StateStore, Protocol):
    """Packets prepared, the actions logged for them, deadlines done (for what is due), once-a-day
    markers, and each target's fit check (state "packet_fit")."""


class Packets:
    """Application packets: which targets are worth preparing, preparing them, redoing a local
    model's stand-in with the cloud, and what the user did with one."""

    def __init__(self, store: PacketStorage, clock: Clock, tz: str, preparer: PacketPreparer | None,
                 plans: Callable[[], Sequence[DomainPlan]], plan: Callable[[str], DomainPlan | None],
                 is_local: Callable[[str | None], bool], report: Callable[[InboxItem, datetime], None],
                 set_status: Callable[[str, str, str], int | None], gate: ActionGate,
                 link_case: Callable[[str, int | None], None], case_feedback: Callable[[str, dict[str, Any]], None],
                 label: Callable[[str | None], str] | None = None, *, words: Language):
        """`is_local`: whether a backend name is a local model; `label`: what the user calls it; `report` tells the user (inbox and push);
        `set_status` changes a target's tracker status, undoably. `link_case` / `case_feedback`: what the
        user does about a fit check (answers its question, sets the packet aside, sends it) is written
        back onto the evaluation case the check was kept as."""
        self.words = words
        self._store = store
        self._clock = clock
        self._tz = tz
        self.preparer = preparer
        self._plans = plans
        self._plan = plan
        self._is_local = is_local
        self._label = label or (lambda name: name or words("prepare.cloud_model"))
        self._report = report
        self._set_status = set_status
        self._gate = gate
        self._link_case = link_case
        self._case_feedback = case_feedback
        self.form_topic: Callable[[int], dict[str, Any]] = lambda pid: {}  # what its application form adds to a packet's topic

    def candidates(self, horizon_days: int = 7, *, include_prepared: bool = False) -> list[tuple[DomainPlan, str]]:
        """Open targets worth preparing now: not applied yet, with a posting
        URL, due (by a deadline or their next action) within the horizon.
        Higher-priority domains and sooner dues first."""
        now = self._clock.now()
        taken = set() if include_prepared else {(p.domain, p.target) for p in self._store.prepared(status=None) if p.kind == PACKET}
        completed = self._store.completed()
        scored = []
        for plan in self._plans():
            today = local_date(now, plan.timezone)
            due_by_target: dict[str, int] = {}
            for st in deadline_statuses(plan, now, completed):
                for t in st.pending_targets:
                    due_by_target[t] = min(due_by_target.get(t, 10_000), st.days_left)
            for item in plan.tracker:
                target = plan.target(item.target)
                if item.status != "todo" or not target or not target.url or (plan.id, item.target) in taken:
                    continue
                days = due_by_target.get(item.target, 10_000)
                if item.next_action_due:
                    days = min(days, (item.next_action_due - today).days)
                if days > horizon_days:
                    continue
                scored.append(((plan.priority, days, target.priority), plan, item.target))
        scored.sort(key=lambda x: x[0])
        return [(plan, t) for _, plan, t in scored]

    def prepare(self, limit: int = 1, only: str | None = None) -> list[Prepared]:
        """Prepares the most urgent packets. With `only`, (re)prepares that
        target whatever its due date, replacing a packet still waiting."""
        if self.preparer is None or limit <= 0:
            return []
        now = self._clock.now()
        made = []
        candidates = self.candidates(horizon_days=10_000, include_prepared=True) if only else self.candidates()
        for plan, target in candidates:
            if only and target != only:
                continue
            item = plan.tracker_item(target)
            if item is None:
                continue
            # what the user asked for is prepared; otherwise only a clear fit, and a doubtful one is asked about once
            if not only and not self._fits(plan, item):
                continue
            p = self.preparer.prepare(plan, item, now, words=self.words)
            if p is None:
                continue
            for old in self._store.prepared(status="ready"):
                if old.kind == PACKET and (old.domain, old.target) == (plan.id, target):
                    self._store.set_prepared_status(old.id, "dismissed")
            p = self._store.add_prepared(p)
            rec = self._store.log_action(
                ActionRecord(None, now, plan.id, self.words("prepare.packet_made", title=p.title), {"kind": "dismiss_prepared", "id": p.id})
            )
            self._link_case(f"action:{rec.id}", (self.fit(plan.id, target) or {}).get("case"))  # undoing it says "not applying"
            self._report(InboxItem(None, now, plan.id, "ready", self.words("prepare.packet_ready", title=p.title), self.words("prepare.packet_check"),
                                   {"prepared": p.id}), now)
            made.append(p)
            if len(made) >= limit:
                break
        return made

    # ── the fit check: a clear fit is prepared on its own, a doubtful one is asked about first ──

    def _fits(self, plan: DomainPlan, item: TrackerItem) -> bool:
        """True when the target may be prepared now: judged a fit, or the user said to. A doubtful
        or poor fit is put to the user once (through the gate, with the reason) and skipped."""
        key = f"{plan.id}/{item.target}"
        fits = dict(self._store.get_state("packet_fit") or {})
        seen = fits.get(key)
        if seen is None and self.preparer is not None:
            judged = self.preparer.judge_fit(plan, item, words=self.words)
            if judged is None:
                return False  # the posting or the model is out of reach: try again later
            entry = {**judged, "at": self._clock.now().isoformat()}
            # onto what is kept now: a check or the user's yes that landed while the model was judging stays
            seen = self._store.update_state("packet_fit", lambda now: {key: entry, **(now or {})})[key]
        if seen is None:
            return False
        if seen.get("decided") == "yes" or seen["verdict"] == "fit":
            return True
        if not seen.get("asked"):
            asked = self._clock.now().isoformat()
            seen = self._store.update_state("packet_fit", lambda now: {**(now or {}), key: {**(now or {}).get(key, seen), "asked": asked}})[key]
            if seen.get("decided") == "yes":
                return True
            ran = self._gate.run(PreparePacket(plan.id, item.target, plan.target_title(item.target), seen.get("why") or ""),
                                 DecidedBy.INFERRED_UNSURE)
            if ran is not None and ran.confirm_id is not None:
                self._link_case(f"confirm:{ran.confirm_id}", seen.get("case"))  # the answer is the truth for this check
        return False

    def fit(self, domain: str, target: str) -> dict[str, Any] | None:
        """The fit check on record for a target (verdict, why, asked, decided)."""
        return (self._store.get_state("packet_fit") or {}).get(f"{domain}/{target}")

    def prepare_anyway(self, domain: str, target: str) -> Prepared | None:
        """The user said yes to a doubtful fit: noted, and prepared now (None when the posting can't be read yet;
        the yes stands and the next round prepares it)."""
        key = f"{domain}/{target}"
        self._store.update_state("packet_fit", lambda now: {**(now or {}), key: {**(now or {}).get(key, {"verdict": "doubt"}), "decided": "yes"}})
        made = self.prepare(limit=1, only=target)
        return made[0] if made else None

    def upgrade_local(self, limit: int = 1) -> list[Prepared]:
        """A packet the local model wrote because the cloud was unreachable is
        a stand-in: with cloud drafting on, redo it once a day until the cloud
        writes it, replacing it quietly (the user was already told it is ready)."""
        if self.preparer is None or not self.preparer.cloud_drafts:
            return []
        now = self._clock.now()
        today = local_date(now, self._tz).isoformat()
        out = []
        for old in self._store.prepared(status="ready"):
            if len(out) >= limit or old.kind != PACKET or not self._is_local(old.prepared_by):
                continue
            if not self._store.claim(f"upgrade:{old.id}:{today}", now):
                continue
            plan = self._plan(old.domain)
            item = plan.tracker_item(old.target) if plan else None
            if item is None or item.status != "todo":
                continue
            new = self.preparer.prepare(plan, item, now, words=self.words)
            if new is None or self._is_local(new.prepared_by):
                continue  # still no cloud; keep the local one
            self._store.set_prepared_status(old.id, "dismissed")
            new = self._store.add_prepared(new)
            self._store.log_action(
                ActionRecord(None, now, plan.id, self.words("prepare.packet_rewritten", model=self._label(new.prepared_by), title=new.title),
                             {"kind": "dismiss_prepared", "id": new.id})
            )
            out.append(new)
        return out

    def get(self, pid: int) -> Prepared | None:
        return self._store.get_prepared(pid)

    def plan_for(self, p: Prepared) -> DomainPlan | None:
        return self._plan(p.domain)

    # what the user did with one, through the gate (the action logged there is the undo)

    def done(self, pid: int) -> Ran | None:
        """The user submitted it: the packet is done and the target applied; undo takes both back."""
        return self._told(pid, self._gate.run(PacketSubmitted(pid), DecidedBy.USER), "sent")

    def dismiss(self, pid: int) -> Ran | None:
        """Set aside: not prepared again for that target (undo brings it back)."""
        return self._told(pid, self._gate.run(PacketSetAside(pid), DecidedBy.USER), "set_aside")

    def _told(self, pid: int, ran: Ran | None, what: str) -> Ran | None:
        """Sending a packet or setting it aside answers its fit check; the action is linked to the
        check's case, so undoing it is written there too."""
        p = self._store.get_prepared(pid)
        if ran is not None and ran.id is not None and p is not None and p.kind == PACKET:
            case = (self.fit(p.domain, p.target) or {}).get("case")
            self._link_case(f"action:{ran.id}", case)
            self._case_feedback(f"action:{ran.id}", {"kind": what})
        return ran

    # the changes themselves, which the gate's actions make

    def mark_sent(self, pid: int) -> tuple[Prepared, int | None] | None:
        p = self._store.get_prepared(pid)
        if p is None or p.status != "ready":
            return None
        self._store.set_prepared_status(pid, "done")
        return p, (self._set_status(p.domain, p.target, "applied") if p.kind == PACKET else None)

    def set_aside(self, pid: int) -> Prepared | None:
        p = self._store.get_prepared(pid)
        if p is None or p.status != "ready":
            return None
        self._store.set_prepared_status(pid, "dismissed")
        return p

    # ── as an actor: a packet can be the topic of a discussion, and the conversation can set it aside or mark it sent ──

    kinds = ("packet",)

    def topic(self, kind: str, ref: str) -> dict[str, Any] | None:
        try:
            p = self._store.get_prepared(int(ref))
        except ValueError:
            return None
        if p is None or p.kind != PACKET:
            return None
        plan = self._plan(p.domain)
        target = plan.target(p.target) if plan else None
        item = plan.tracker_item(p.target) if plan else None
        packet = p.data.get("packet") or {}
        return {"kind": "packet", "ref": p.id, "domain": p.domain, "target": p.target, "title": p.title, "status": p.status,
                "posting": p.data.get("posting"), "target_notes": target.notes if target else None,
                "tracker": {"status": item.status, "next_action": item.next_action} if item else None,
                **{k: packet[k] for k in ("summary", "match", "gaps", "ask_first") if k in packet},
                "answers": [a.get("q") for a in packet.get("answers") or []],
                **self.form_topic(p.id),
                "note": "This is an application packet Muninn prepared for the user that has not been sent yet (the opening was picked "
                        "while planning, not chosen by the user). On whether to apply and whether the user meets the requirements, speak plainly "
                        "from the posting as written and the user's situation; do not talk the user into applying to make progress. "
                        "When the user decides not to apply, use dismiss_packet; when the user says it has been sent, use packet_sent."}

    def tools(self) -> list[Tool]:
        return [DismissPacket(self), PacketSent(self)]

    def jobs(self, per_tick: int = 1) -> list[Any]:
        """Preparing the most urgent packets (`per_tick` a tick), or else redoing a local model's stand-in."""
        from .actors import Job

        def prepare() -> None:
            if not self.prepare(per_tick):
                self.upgrade_local(1)

        return [Job("preparing packets", prepare, phase="after")]
