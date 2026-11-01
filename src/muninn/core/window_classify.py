"""Which domain and goal a window belongs to, from its app and title.

Window titles name documents, channels and people (sensitive: local unless
the router's policy is cloud-first). Each
title is judged with what else the person was doing at the time (the domains
of nearby activity) and with titles whose area is already settled, and the
model says whether it is sure. An unsure answer counts toward nothing until a
second look agrees (Engine.recheck_windows)."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any, Callable, Protocol, Sequence

from .attribution import Attributor, is_generic_window, window_key
from .cases import CaseFeedback
from .llm_router import LLMRequest, LLMRouter
from .metrics import span_of
from .models import AGENT_ACTIVITY_KINDS, ActionRecord, Confirmation, DomainPlan, Observation, Sensitivity
from .privacy import UNDERSTANDING_DOMAIN, Cleared
from .timewin import local
from .ports import ActionLog, Clock, ConfirmationStore, ObservationStore, StateStore
from .language import Language
from .reply_json import json_object

SYSTEM = """You sort a person's computer windows into the areas of their life.
Input JSON: domains (id, title), goals (id, title; time spent counts toward them), examples (titles whose domain is settled), windows (app, title, nearby: the domains of the person's other activity within 15 minutes, with counts).
Reply with one JSON object and nothing else: {"windows": [{"title": "<the window title, verbatim>", "domain": "<domain id or null>", "goal": "<goal id or null>", "sure": true|false}]}
- sure: true when the title itself makes the area clear, or the title fits the nearby activity.
- nearby is what the person was doing around it: a hint for a title that could belong to more than one area (a GitHub profile, a doc with a vague name).
- domain null when the window says nothing about what the person is working on (a browser start page, a system dialog, generic entertainment).
- goal only when the window is clearly work on that goal (e.g. reading a paper for a research-hours goal); otherwise null.
- Chats, tickets and tools of the person's employer belong to the work domain."""


class WindowClassifier:
    def __init__(self, router: LLMRouter):
        self._router = router

    def classify(self, windows: Sequence[dict[str, Any]], domains: Sequence[tuple[str, str]],
                 goals: Sequence[dict[str, str]], examples: Sequence[dict[str, Any]] = ()) -> tuple[dict[str, dict[str, Any]] | None, int | None]:
        """Labels by title, and the evaluation case the call was kept as."""
        prompt = json.dumps({"domains": [{"id": d, "title": t} for d, t in domains], "goals": list(goals),
                             "examples": list(examples), "windows": list(windows)}, ensure_ascii=False)
        c = self._router.complete_case(LLMRequest(SYSTEM, prompt, Sensitivity.SENSITIVE, None, json_mode=True, task="window_classify"))
        data = json_object(c.reply)
        if data is None:
            return None, c.case
        ids = {d for d, _ in domains}
        goal_ids = {g["id"] for g in goals}
        out = {}
        for w in data.get("windows") or []:
            if not isinstance(w, dict) or "title" not in w:
                continue
            domain = w.get("domain") if w.get("domain") in ids else None
            goal = w.get("goal") if w.get("goal") in goal_ids and domain and str(w["goal"]).startswith(f"{domain}/") else None
            out[str(w["title"])] = {"domain": domain, "goal": goal, "sure": w.get("sure") is True}
        return out, c.case


WINDOW_ASK_HOURS = 1.0  # a disputed window is worth the user's time only above this much in a week


class WindowStorage(ObservationStore, StateStore, ConfirmationStore, ActionLog, Protocol):
    """The window observations, the labels learned so far (state "window_classes"), the questions asked, and the answers logged."""


class WindowLabels:
    """Which domain a window title belongs to: the local model's look (twice when unsure),
    the work around a title that names nothing, and the user's answer, which beats both."""

    def __init__(self, store: WindowStorage, clock: Clock, classifier: WindowClassifier | None, cases: CaseFeedback,
                 all_plans: Callable[[], Sequence[DomainPlan]], domains: Callable[[], Sequence[tuple[str, str]]],
                 attributor: Callable[[], Attributor], reattribute: Callable[[], int], *, tz: str = "UTC",
                 clear: Callable[[str], Cleared | None] = lambda text: None, words: Language):
        """`attributor`: the current attribution rules; `reattribute`: attributes what is still unattributed
        (with the labels just learned), returning how many; `tz` and `clear` (the work check) are for
        the question topic."""
        self.words = words
        self._store = store
        self._clock = clock
        self.classifier = classifier
        self._cases = cases
        self._all_plans = all_plans
        self._domains = domains
        self._attributor = attributor
        self._reattribute = reattribute
        self._tz = tz
        self._clear = clear  # the work check: may the cloud read this text

    def classify(self, limit: int = 25) -> int:
        """Asks the local model which domain (and goal) unattributed window
        titles belong to, once per title, with what else was going on at the
        time. Window titles never leave the machine; they stay sensitive
        whatever the answer."""
        if self.classifier is None:
            return 0
        known = dict(self._store.get_state("window_classes") or {})
        todo: dict[str, dict[str, Any]] = {}
        for o in self._store.unattributed(2000):
            if o.kind != "window":
                continue
            key = window_key(o.payload)
            if is_generic_window(o.payload):
                continue  # says nothing by itself: follows the work around it (attribute_by_context)
            if key not in known and key not in todo:
                todo[key] = self._window_item(o)
            if len(todo) >= limit:
                break
        if not todo:
            return 0
        result = self._classify(list(todo.values()), known)
        if result is None:
            return 0
        judged = {key: {"domain": (hit := result.get(item["title"]) or {}).get("domain"), "goal": hit.get("goal"),
                        "sure": bool(hit.get("sure")), "by": "model", "looks": 1} for key, item in todo.items()}
        # onto what is kept now: an answer the user gave while the model was thinking is theirs, not the model's
        self._store.update_state("window_classes", lambda now: {**judged, **(now or {})})
        return self._reattribute()

    def by_context(self, limit: int = 5000) -> int:
        """Windows whose title names nothing (the Claude app is always just
        "Claude", a bare terminal) belong to whatever project was active in
        the same minutes: the domain of the attributed Claude Code activity
        and commits within 10 minutes, when one domain clearly leads."""
        pad = timedelta(minutes=10)
        n = 0
        for o in self._store.unattributed(limit):
            if o.kind != "window" or not is_generic_window(o.payload):
                continue
            near: dict[tuple[str, str | None], int] = {}
            for x in self._store.observations(o.ts - pad, span_of(o)[1] + pad, kinds=(*AGENT_ACTIVITY_KINDS, "commit")):
                if x.domain:
                    near[(x.domain, x.goal)] = near.get((x.domain, x.goal), 0) + 1
            if not near:
                continue
            (domain, goal), top = max(near.items(), key=lambda kv: kv[1])
            if top * 3 >= 2 * sum(near.values()):
                self._store.reattribute(o.key, domain, goal, o.sensitivity)
                n += 1
        return n

    def recheck(self, limit: int = 15) -> int:
        """A second look at labels the model was unsure of, labels from before
        it said so, and labels the surrounding activity disagrees with. Two
        looks that agree settle it; when they disagree, the window counts
        toward nothing, and only a window worth an hour a week is asked about."""
        if self.classifier is None:
            return 0
        known = dict(self._store.get_state("window_classes") or {})
        todo: dict[str, dict[str, Any]] = {}
        for key, v in known.items():
            if v.get("by") == "user" or v.get("looks", 1) >= 2:
                continue
            app, _, title = key.partition("\t")
            if is_generic_window({"app": app, "title": title}):
                continue
            seen = self._store.observations_for_window(app, title)
            if not seen:
                continue
            item = self._window_item(seen[-1])
            near = item["nearby"]
            top = max(near, key=near.get) if near else None
            disputed = v.get("domain") and top and top != v["domain"] and near[top] >= 3 * near.get(v["domain"], 0) + 3
            if v.get("sure") is not True or disputed:
                todo[key] = item
            if len(todo) >= limit:
                break
        if not todo:
            return 0
        result = self._classify(list(todo.values()), known)
        if result is None:
            return 0
        changed = 0
        since = self._clock.now() - timedelta(days=7)
        for key, item in todo.items():
            first, second = known[key], result.get(item["title"]) or {}
            if second.get("domain") == first.get("domain"):
                known[key] = {**first, "sure": bool(first.get("domain")) or bool(second.get("sure")), "looks": 2}
            else:
                known[key] = {"domain": None, "goal": None, "sure": False, "by": "model", "looks": 2,
                              "options": sorted({d for d in (first.get("domain"), second.get("domain")) if d})}
                app, _, title = key.partition("\t")
                hours = sum((e - s).total_seconds() for s, e in map(span_of, self._store.observations_for_window(app, title))
                            if s >= since) / 3600
                if hours >= WINDOW_ASK_HOURS:
                    self._ask_window(key, item, known[key]["options"], hours)
            changed += 1
        self._store.set_state("window_classes", known)
        for key in todo:
            self.relabel(key)
        return changed

    def _window_item(self, o: Observation) -> dict[str, Any]:
        """A window as the model sees it: app, title, and the domains of the
        other activity within 15 minutes of it."""
        near: dict[str, int] = {}
        pad = timedelta(minutes=15)
        key = window_key(o.payload)
        for x in self._store.observations(o.ts - pad, o.ts + pad):
            if x.domain and x.key != o.key and not (x.kind == "window" and window_key(x.payload) == key):
                near[x.domain] = near.get(x.domain, 0) + 1
        return {"app": o.payload.get("app"), "title": str(o.payload.get("title") or "")[:120], "nearby": near}

    def _classify(self, items: list[dict[str, Any]], known: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
        goals = [{"id": f"{p.id}/{g.id}", "title": g.title} for p in self._all_plans() for g in p.goals if g.metric == "hours"]
        examples = [{"title": k.partition("\t")[2], "domain": v["domain"]}
                    for k, v in known.items() if v.get("by") == "user" and v.get("domain")][:15]
        if len(examples) < 15:  # windows the config rules placed: settled too
            for o in self._store.observations(self._clock.now() - timedelta(days=7), kinds=("window",))[-400:]:
                if o.domain and window_key(o.payload) not in known and len(examples) < 15:
                    examples.append({"title": str(o.payload.get("title") or "")[:120], "domain": o.domain})
        result, case = self.classifier.classify(items, self._domains(), goals, examples)
        for it in items:  # the user's later answer about a window counts against this call
            self._cases.link(f"window:{it['app']}\t{it['title']}", case)
        return result

    def relabel(self, key: str) -> None:
        app, _, title = key.partition("\t")
        attr = self._attributor()
        for o in self._store.observations_for_window(app, title):
            a = attr.attribute(o.with_attribution(None, None, o.sensitivity))
            if (a.domain, a.goal) != (o.domain, o.goal):
                self._store.reattribute(o.key, a.domain, a.goal, a.sensitivity)

    def _ask_window(self, key: str, item: dict[str, Any], options: list[str], hours: float) -> None:
        titles = dict(self._domains())
        labels = {titles.get(d, d): d for d in options}
        self._store.add_confirmation(Confirmation(
            None, self._clock.now(), "window_class",
            self.words("window_classify.which_domain", title=item["title"][:60], app=item["app"], hours=hours),
            (*labels, self.words("window_classify.none")), {"window": key, "domains": labels}, key=f"window:{key}"))

    def set_domain(self, key: str, domain: str | None) -> None:
        """The user's answer: it beats any model's, and is used as an example from now on."""
        mine = {"domain": domain, "goal": None, "sure": domain is not None, "by": "user", "looks": 2}
        self._store.update_state("window_classes", lambda now: {**(now or {}), key: mine})
        self._cases.record(f"window:{key}", {"kind": "window_label", "title": key.partition("\t")[2], "domain": domain})
        self.relabel(key)

    def question_topic(self, c: Confirmation) -> dict[str, Any]:
        """A window question with what Muninn knows about that window: when it
        was open, and what else was going on then (domains, and the projects
        of Claude Code sessions and commits nearby)."""
        app, _, title = str(c.data["window"]).partition("\t")
        seen = self._store.observations_for_window(app, title)[-40:]
        pad = timedelta(minutes=10)
        domains: dict[str, int] = {}
        projects: dict[str, int] = {}
        for o in seen:
            for x in self._store.observations(o.ts - pad, span_of(o)[1] + pad):
                if x.kind == "window" and window_key(x.payload) == c.data["window"]:
                    continue
                if x.domain:
                    domains[x.domain] = domains.get(x.domain, 0) + 1
                if x.payload.get("repo"):
                    projects[str(x.payload["repo"])] = projects.get(str(x.payload["repo"]), 0) + 1
        times = [f"{local(o.ts, self._tz):%m/%d %H:%M} ({float(o.payload.get('duration_s', 0)) / 60:.0f} min)" for o in seen[-8:]]
        cleared = self._clear(f"{app}: {title}")
        return {"kind": "question", "ref": c.id, "domain": UNDERSTANDING_DOMAIN if cleared else None,
                "question": c.question, "options": list(c.options),
                "window": {"app": app, "title": title, "recent_times": times,
                           "nearby_domains": domains, "nearby_projects": projects},
                "note": "This is a question of which domain a window belongs to. Use the data in window to say what you know; "
                        "do not use answer_question until the user has plainly picked one of the options."}

    def answered(self, c: Confirmation, answer: str) -> None:
        """The user said which domain a window belongs to (undoable)."""
        prev = (self._store.get_state("window_classes") or {}).get(c.data["window"])
        self.set_domain(c.data["window"], (c.data.get("domains") or {}).get(answer))
        self._store.log_action(ActionRecord(None, self._clock.now(), "", self.words("window_classify.labelled", window=c.data["window"].partition(chr(9))[2][:40], domain=answer),
                                            {"kind": "window_label", "key": c.data["window"], "prev": prev}))

    def jobs(self) -> list[Any]:
        from .actors import Job

        def label() -> None:
            self.by_context()
            self.classify()
            self.recheck()

        return [Job("window classification", label)]
