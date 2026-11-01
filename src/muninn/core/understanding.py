"""Understanding the user's own words, and which model may read them.

Commands, facts and questions are read by the best model the user allows.
With the cloud allowed, a WorkCheck looks at the words first: the work
patterns from the attribution rules, then the local model. Only words it
clears go to the cloud, together with context built without the work
domains. Anything it cannot clear is read locally, as before.

Every call is kept as an evaluation case by the router (evals.py).
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Iterable, Sequence

from .llm_router import Completion, LLMRequest, LLMRouter
from .models import Attachment, Sensitivity
from .privacy import UNDERSTANDING_DOMAIN, Cleared, understanding_payload
from .reply_json import json_object

WORK_SYSTEM = """The user keeps these life domains. Say which one a sentence they wrote is about.
The work domains are their current job itself: its colleagues, clients, tickets, meetings, code and deadlines; the rest of their life (looking for a job included) is in the other domains.
Reply with one JSON object and nothing else: {"domain": "<domain id>"}, or {"domain": "none"} for small talk that belongs to no domain."""


class WorkCheck:
    def __init__(self, router: LLMRouter, work_domains: Iterable[str], domains: Callable[[], Sequence[tuple[str, str]]],
                 patterns: Iterable[str], projects: dict[str, str] | None = None):
        self._router = router
        self._projects = dict(projects or {})  # project name → domain id
        self._work = set(work_domains)
        self._domains = domains  # (id, title) of every domain, read when needed
        self._patterns = [re.compile(p) for p in patterns]

    @property
    def domain_ids(self) -> set[str]:
        return set(self._work)

    def matches(self, text: str) -> bool:
        """The work patterns alone: cheap, for context lines such as block titles."""
        return any(p.search(text) for p in self._patterns)

    def clear(self, text: str) -> Cleared | None:
        if self.matches(text):
            return None
        domains = self._domains()
        prompt = json.dumps({"work_domains": [{"id": d, "title": t} for d, t in domains if d in self._work],
                             "other_domains": [{"id": d, "title": t} for d, t in domains if d not in self._work],
                             "projects": self._projects, "text": text}, ensure_ascii=False)
        # the words are not cleared yet: local model only
        reply = self._router.complete(LLMRequest(WORK_SYSTEM, prompt, Sensitivity.SENSITIVE, None, json_mode=True, task="work_check"))
        d = json_object(reply)
        if d is None:
            return None
        domain = d.get("domain")
        known = {d for d, _ in domains} | {"none"}
        return Cleared(text) if domain in known and domain not in self._work else None

    def clear_domain(self, domain: str, text: str) -> Cleared | None:
        """Material from one non-work domain's own plan (no user words to check)."""
        return None if domain in self.domain_ids or self.matches(text) else Cleared(text)


class Understanding:
    def __init__(self, router: LLMRouter, work_check: WorkCheck | None = None):
        self._router = router
        self.work_check = work_check  # None: no gate (all local, or all cloud, as the router's policy says)

    @property
    def router(self) -> LLMRouter:
        return self._router

    def clear(self, text: str) -> Cleared | None:
        return self.work_check.clear(text) if self.work_check else None

    def clear_domain(self, domain: str, text: str) -> Cleared | None:
        return self.work_check.clear_domain(domain, text) if self.work_check else None

    def is_work_line(self, text: str) -> bool:
        return bool(self.work_check and self.work_check.matches(text))

    def complete(self, task: str, system: str, data: dict[str, Any], cleared: Cleared | None,
                 *, json_mode: bool = False, attachments: Sequence[Attachment] = ()) -> Completion:
        """The reply, the evaluation case it was kept as, and whether a local model stood in or the
        cloud's login lapsed. Not cleared: sensitive, so the routing decides where it is read; so are the
        files the user sent (nothing clears a photo)."""
        payload = understanding_payload(data, cleared) if cleared and not attachments else None
        if payload is not None:
            req = LLMRequest(system, payload, Sensitivity.PERSONAL, UNDERSTANDING_DOMAIN, json_mode, task)
        else:
            req = LLMRequest(system, json.dumps(data, ensure_ascii=False, default=str), Sensitivity.SENSITIVE, None, json_mode, task,
                             attachments=tuple(attachments))
        return self._router.complete_case(req)
