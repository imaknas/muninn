"""Which to-dos are the same task written down in more than one place.

The projects keep their own to-do lists, and Muninn keeps deadlines and next
actions of its own; the same task ("ask the bank adviser three things")
often appears in several of them in different words. The model groups them
once per change of the lists (the answer is kept, keyed by the lists' text),
so the home page can show each task once: not at all when it is Muninn's own
item, which Muninn shows in its own places, and once for the first project
when several projects list it.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

from .llm_router import LLMRequest, LLMRouter
from .models import Sensitivity
from .ports import StateStore
from .reply_json import json_object

SYSTEM = """Some to-dos are the same task written down in more than one list. Find them.
Input: muninn (the assistant's own open items, with ids) and todos (other projects' to-dos, with ids).
Reply with one JSON object and nothing else:
{"same": [{"todo": "<todo id>", "as": "<muninn item id, or another todo id>"}]}
- Two to-dos are the same task when doing one does the other: the same action on the same thing (the same question to the same person, the same document to find).
- For two todos that are the same, point the later one in the list at the earlier one."""


class TodoMatcher:
    def __init__(self, router: LLMRouter, store: StateStore):
        self._router = router
        self._store = store

    def match(self, muninn: Sequence[dict[str, str]], todos: Sequence[dict[str, str]]) -> dict[str, str]:
        """todo id → the id it duplicates; computed only when the lists changed."""
        data = {"muninn": list(muninn), "todos": list(todos)}
        key = hashlib.sha1(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
        cached = self._store.get_state("todo_match") or {}
        if cached.get("key") == key:
            return dict(cached.get("same") or {})
        req = LLMRequest(SYSTEM, json.dumps(data, ensure_ascii=False), Sensitivity.SENSITIVE, None, json_mode=True, task="todo_match")
        reply, backend = self._router.complete_with_backend(req)
        same = _parse(reply, {m["id"] for m in muninn}, [t["id"] for t in todos])
        # a local fallback where the cloud was meant is not kept: the next tick asks again
        fallback = self._router.cloud_allowed(req) and self._router.is_local(backend)
        if same is not None and not fallback:
            self._store.set_state("todo_match", {"key": key, "same": same})
        return same or {}

    def known(self) -> dict[str, str]:
        """The last answer, without asking (for the home page)."""
        return dict((self._store.get_state("todo_match") or {}).get("same") or {})


def _parse(reply: str | None, muninn: set[str], order: list[str]) -> dict[str, str] | None:
    d = json_object(reply)
    if d is None:
        return None
    pos = {t: i for i, t in enumerate(order)}
    out = {}
    for x in d.get("same") or []:
        if not isinstance(x, dict):
            continue
        todo, other = str(x.get("todo")), str(x.get("as"))
        if todo not in pos or todo == other:
            continue
        if other in muninn or (other in pos and pos[other] < pos[todo]):  # earlier todos stay, later ones go
            out[todo] = other
    return out


TASKS_SYSTEM = """New tasks for the user arrived. Say which of them is the same task as one already known.
Input: new (key, text, projects) and known (key, text, projects, status open or done).
Reply with one JSON object and nothing else: {"same": [{"new": "<new key>", "as": "<known key>"}]}
- Two tasks are the same when doing one does the other; a new task with none is left out."""


class ModelTaskMatch:
    """For TaskRegistry.sort_new: new tasks against known ones, one call."""

    def __init__(self, router: LLMRouter):
        self._router = router

    def __call__(self, new: Sequence[dict[str, Any]], known: Sequence[dict[str, Any]]) -> dict[str, str] | None:
        """None when there is no trustworthy answer: no reply, an unreadable
        one, or only the local fallback's where the cloud was meant (a wrong
        merge hides a task, so a weaker model may not make one)."""
        fresh = {t["key"] for t in new}
        data = {"new": list(new), "known": [t for t in known if t["key"] not in fresh]}  # never offered as its own match
        req = LLMRequest(TASKS_SYSTEM, json.dumps(data, ensure_ascii=False), Sensitivity.SENSITIVE, None, json_mode=True, task="task_match")
        reply, backend = self._router.complete_with_backend(req)
        if not reply or (self._router.cloud_allowed(req) and self._router.is_local(backend)):
            return None
        d = json_object(reply)
        if d is None:
            return None
        new_keys, known_keys = fresh, {t["key"] for t in data["known"]}
        return {str(x["new"]): str(x["as"]) for x in d.get("same") or []
                if isinstance(x, dict) and x.get("new") in new_keys and x.get("as") in known_keys}
