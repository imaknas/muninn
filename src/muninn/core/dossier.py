"""Everything Muninn knows about an item the user is discussing.

A discussion is only as good as what the model can see. For an item in a
domain that is the domain's whole plan (targets with their notes, the
tracker, pivot, decision tree, filters), the other items still waiting for
the user, progress, the state of the projects in that domain, the packets
Muninn prepared, the text of the job postings named in the plan (cached for
a week) and the user's own reference documents.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Protocol, Sequence

from .answer import plan_digest
from .models import DomainPlan, Goal
from .ports import Clock, PreparedStore, StateStore
from .prepare import PacketPreparer
from .progress import GoalProgress
from .language import Language

POSTING_CHARS = 6_000
POSTINGS = 6
DOCUMENT_CHARS = 5_000
STATE_CHARS = 5_000
POSTING_TTL = timedelta(days=7)


class DossierStorage(PreparedStore, StateStore, Protocol):
    """The packets prepared, and the postings' text cached in state."""


class Dossier:
    def __init__(self, store: DossierStorage, clock: Clock, plan: Callable[[str], DomainPlan | None],
                 review_queue: Callable[[], Sequence[dict[str, Any]]],
                 progress: Callable[[DomainPlan, Goal, datetime], GoalProgress | None],
                 projects: Callable[[], dict[str, list[str]]], project_state: Callable[[str], str | None],
                 preparer: Callable[[], PacketPreparer | None], *, words: Language):
        """`plan`: a domain's plan, unconfirmed items included; `review_queue`: items waiting for the
        user; `progress`: a goal's progress now; `projects`: projects per domain; `project_state`: a
        project's state summary; `preparer`: fetches postings and holds the user's documents."""
        self.words = words  # the language Muninn speaks to the user in
        self._store = store
        self._clock = clock
        self._plan = plan
        self._review_queue = review_queue
        self._progress = progress
        self._projects = projects
        self._project_state = project_state
        self._preparer = preparer

    def build(self, domain: str | None) -> dict[str, Any]:
        plan = self._plan(domain) if domain else None
        if plan is None:
            return {}
        out: dict[str, Any] = {"plan": plan_digest(plan)}
        out["waiting_for_review"] = [{"title": x["title"], "detail": x["detail"], "source": x.get("source")}
                                     for x in self._review_queue() if x["domain"] == domain]
        from .reports import goal_line

        now = self._clock.now()
        out["progress"] = [goal_line(gp, self.words)[2:] for g in plan.goals if (gp := self._progress(plan, g, now))]
        states = {}
        for project in self._projects().get(domain, []):
            if (st := self._project_state(project)):
                states[project] = st[:STATE_CHARS]
        if states:
            out["project_states"] = states
        packets = [{"target": p.target, "title": p.title, "status": p.status,
                    **{k: v for k, v in (p.data.get("packet") or {}).items() if k in ("summary", "match", "gaps", "ask_first")}}
                   for p in self._store.prepared(status=None) if p.domain == domain]
        if packets:
            out["prepared_packets"] = packets[-10:]
        postings = [self._posting(t.url) | {"target": t.id} for t in plan.targets if t.url][:POSTINGS]
        if postings := [p for p in postings if p.get("text")]:
            out["postings"] = postings
        if plan.targets and (preparer := self._preparer()) is not None:
            out["my_documents"] = [{"name": n, "text": t[:DOCUMENT_CHARS]} for n, t in preparer.documents()]
        return out

    def _posting(self, url: str) -> dict[str, Any]:
        """A posting's text, from the cache when fetched within the week."""
        key = f"posting:{url}"
        now = self._clock.now()
        cached = self._store.get_state(key)
        if cached and now - datetime.fromisoformat(cached["at"]) < POSTING_TTL:
            return cached["posting"]
        posting = None
        if (preparer := self._preparer()) is not None:
            try:
                posting = preparer.fetch_posting(url)
            except Exception:  # an unreachable board must not break a discussion
                posting = None
        data = {"url": url, "title": posting.title, "text": posting.text[:POSTING_CHARS]} if posting else {"url": url}
        self._store.set_state(key, {"at": now.isoformat(), "posting": data})
        return data
