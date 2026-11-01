"""Noticing that a task got done without being told.

At each tick, what happened since the last look (conversation digests from
the user's working sessions, mail, and the meetings in their own calendars
that have ended) is compared with the open tasks in one model call. Muninn's
own calendar blocks are not evidence: a block ending says it was planned,
not that it was done. A task the evidence clearly
shows done is closed at once, the evidence named in the why, and listed
among what Muninn did, with an undo. Unclear evidence leaves it open; nothing
is asked.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

from .llm_router import LLMRequest, LLMRouter
from .models import Sensitivity
from .ports import Clock, ObservationStore, StateStore, TurnStore
from .tasks import TaskRegistry
from .language import Language, written_in
from .reply_json import json_object

SYSTEM = """These things just happened in the user's life. Which of the open tasks do they show the user has DONE?
Reply with one JSON object and nothing else: {"done": [{"task": "<task key>", "why": "<one sentence in {language}, saying which evidence it is>"}]}
- Done means evidence that the task itself was done (the question was asked and answered, the document was sent, the meeting with that person happened).
- A task with several parts (several companies, several questions) is done only when every part is.
- None done: {"done": []}."""
FIRST_LOOK = timedelta(days=2)  # how far back the very first pass looks


def system(words: Language) -> str:
    return written_in(SYSTEM, words)


class EvidenceStorage(TurnStore, ObservationStore, StateStore, Protocol):
    """Conversation digests and mail notes (the evidence), and how far the last look reached."""


class CompletionDetector:
    def __init__(self, store: EvidenceStorage, clock: Clock, tasks: TaskRegistry, router: LLMRouter,
                 calendar: Callable[[datetime, datetime], list[dict[str, Any]]], link_case: Callable[[str, int | None], None], *, words: Language):
        """`router`: the understanding model's (the stronger one: a wrong close hides a task); `calendar`:
        the user's own calendar events (Muninn's blocks left out); `link_case`: ties an action to the case that made it."""
        self.words = words
        self._store = store
        self._clock = clock
        self._tasks = tasks
        self._router = router
        self._calendar = calendar
        self._link_case = link_case

    def evidence(self, since: datetime, until: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for d in self._store.digests(since, until):
            out.append({"kind": "conversation", "project": d.get("project"), "summary": d.get("summary"),
                        "decisions": d.get("decisions"), "commitments": d.get("commitments")})
        for o in self._store.observations(since, until, kinds=("mail",)):
            if o.payload.get("category") not in ("promo",):
                out.append({"kind": "mail", "from": o.payload.get("from"), "subject": o.payload.get("subject"),
                            "summary": o.payload.get("summary")})
        out += [{"kind": "meeting", **ev} for ev in self._calendar(since, until)
                if ev["start"] <= f"{until:%Y-%m-%d %H:%M}"]  # ones that took place (Muninn's blocks are not in it)
        return out

    def run(self) -> int:
        now = self._clock.now()
        cursor = self._store.get_state("tasks:evidence_since")
        # never further back than FIRST_LOOK: tasks that arrive later (a migration) still meet recent evidence
        since = max(datetime.fromisoformat(cursor), now - FIRST_LOOK) if cursor else now - FIRST_LOOK
        tasks = self._tasks.open()
        if not tasks:
            return 0  # nothing to close; the evidence waits for the tasks that may arrive
        found = self.evidence(since, now)
        if not found:
            self._store.set_state("tasks:evidence_since", now.isoformat())
            return 0
        data = {"open_tasks": [{"key": t["key"], "text": t["title"], "context": t["context"]} for t in tasks], "evidence": found}
        req = LLMRequest(system(self.words), json.dumps(data, ensure_ascii=False, default=str), Sensitivity.SENSITIVE, None,
                         json_mode=True, task="task_evidence")
        router = self._router
        c = router.complete_case(req)
        reply, backend = c.reply, c.backend
        if reply is None or (router.cloud_allowed(req) and router.is_local(backend)):
            return 0  # no answer, or only the fallback's: look at the same stretch next time
        parsed = json_object(reply)
        if parsed is None:
            return 0  # an answer that cannot be read is no answer: the same stretch is looked at next time
        done = parsed.get("done") or []
        keys = {t["key"] for t in tasks}
        n = 0
        for x in done:
            if isinstance(x, dict) and x.get("task") in keys:
                if rec := self._tasks.close(str(x["task"]), by="evidence", why=str(x.get("why") or "")[:200]):
                    self._link_case(f"action:{rec.id}", c.case)  # an undo counts against this judgment
                    n += 1
        self._store.set_state("tasks:evidence_since", now.isoformat())
        return n
