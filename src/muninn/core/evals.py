"""Measuring a model against what Muninn was asked before.

Each understanding call was kept as a case: the request, the reply, the
model that wrote it, and later the user's corrections (an undone action, a
cancelled handoff, a "不是"). The reference for a case is the reply the cloud
model gave, minus what the user corrected; a case whose action the user
undid has no reference left and is skipped. A candidate model (normally the
local one) answers the same requests and is scored per task:

- route: the same tools with the same ids (targets, goals, deadlines, blocks)
- facts: precision and recall of (item, effect) pairs
- answer / why: a judge compares the candidate's answer with the reference
- a decision point (packet_fit): the same outcome as the reference, and the
  user's own word (their answer, what they did with the result) replaces the
  cloud's reply as the reference wherever there is one

Each call's time is kept too, so a cheaper model can be weighed on cost as
well as on how often it is right.

A candidate that is not local only ever sees cases that were cleared for the
cloud when they were recorded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
import time
from typing import Any, Callable, Protocol

from .commands import read_route as parse_route
from .llm_router import LLMRequest, LLMRouter
from .models import Sensitivity
from .ports import Clock, LLMBackend, EvalStore
from .privacy import UNDERSTANDING_DOMAIN, Cleared, understanding_payload
from .redact import redact
from .reply_json import json_object

ID_ARGS = ("target", "goal", "deadline", "block", "status", "domain", "op", "id")


class CaseRecorder:
    """Keeps every model request and reply as an evaluation case (injected into the routers)."""

    def __init__(self, store: EvalStore, clock: Clock):
        self._store = store
        self._clock = clock

    def __call__(self, req: LLMRequest, reply: str | None, backend: str | None, *, by_local: bool, cloud_ok: bool,
                 ms: int | None = None) -> int:
        text = req.content.text if hasattr(req.content, "text") else str(req.content)
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            data = {RAW: text}
        tokens = (getattr(reply, "input_tokens", None), getattr(reply, "output_tokens", None))  # when the provider said
        return self._store.add_eval_case(self._clock.now(), req.task, req.system, data, reply, backend,
                                         by_local=by_local, cloud_ok=cloud_ok, json_mode=req.json_mode, ms=ms, tokens=tokens)


RAW = "_raw"  # a request whose content was not a JSON object


def request_text(case: dict[str, Any]) -> str:
    d = case["data"]
    return str(d[RAW]) if set(d) == {RAW} else json.dumps(d, ensure_ascii=False, default=str)


class Judge(Protocol):
    def score(self, case: dict[str, Any], reference: str, candidate: str) -> float | None:
        """0..1, how well the candidate answers compared with the reference."""
        ...


def _route_keys(raw: str | None) -> set[tuple[str, tuple[tuple[str, str], ...]]] | None:
    plan = parse_route(raw)
    if plan is None:
        return None
    return {(a["tool"], tuple(sorted((k, str(v)) for k, v in (a.get("args") or {}).items() if k in ID_ARGS)))
            for a in plan["actions"]}


def _fact_pairs(raw: str | None) -> set[tuple[str, str]] | None:
    d = json_object(raw)
    if d is None:
        return None
    return {(str(x.get("id")), str(x.get("effect"))) for x in d.get("effects") or [] if isinstance(x, dict)}


def _f1(ref: set[Any], got: set[Any]) -> float:
    if not ref and not got:
        return 1.0
    hit = len(ref & got)
    if hit == 0:
        return 0.0
    p, r = hit / len(got), hit / len(ref)
    return 2 * p * r / (p + r)


class Scorer(Protocol):
    """How one task's replies are compared. `keys` turns a reply into what is
    compared (None: unreadable); `adjust` applies the user's corrections to
    the reference keys (None: no trustworthy reference left)."""

    def keys(self, raw: str | None) -> Any: ...

    def adjust(self, case: dict[str, Any], ref: Any) -> Any: ...

    def score(self, ref: Any, got: Any) -> float: ...


class RouteScorer:
    def keys(self, raw):
        return _route_keys(raw)

    def adjust(self, case, ref):
        rejected = any(f.get("kind") == "answer" and not f.get("accepted") for f in case["feedback"])
        return None if rejected else ref

    def score(self, ref, got):
        return _f1(ref, got)


class FactsScorer:
    def keys(self, raw):
        return _fact_pairs(raw)

    def adjust(self, case, ref):
        texts = {i["id"]: i.get("text", "") for i in case["data"].get("open_items") or []}
        for f in case["feedback"]:  # what the user turned down is not part of the reference
            if f.get("kind") == "handoff_cancelled" and f.get("item"):
                ref = {p for p in ref if texts.get(p[0]) != f["item"]}
            if f.get("kind") == "answer" and not f.get("accepted"):
                ref = {p for p in ref if not texts.get(p[0]) or texts[p[0]] not in f.get("question", "")}
        return ref

    def score(self, ref, got):
        return _f1(ref, got)


class FieldScorer:
    """One field of a JSON reply must match (a domain, a mail category)."""

    def __init__(self, field: str):
        self.field = field

    def keys(self, raw):
        d = json_object(raw)
        return None if d is None else d.get(self.field)

    def adjust(self, case, ref):
        return ref

    def score(self, ref, got):
        return 1.0 if ref == got else 0.0


class WindowScorer:
    """The share of window titles put in the same domain; the user's own
    answers about a window replace the model's."""

    def keys(self, raw):
        d = json_object(raw)
        if d is None:
            return None
        return {str(w.get("title")): w.get("domain") for w in d.get("windows") or [] if isinstance(w, dict)}

    def adjust(self, case, ref):
        for f in case["feedback"]:
            if f.get("kind") == "window_label" and f.get("title") in ref:
                ref = {**ref, f["title"]: f.get("domain")}
        return ref

    def score(self, ref, got):
        return sum(got.get(t) == d for t, d in ref.items()) / len(ref) if ref else 1.0


class LabelScorer:
    """A decision point: one field of the reply is the label, and what is compared is the
    outcome the label leads to (several labels may lead to the same one). `truth(case)` reads
    the user's own word from the case's feedback: the outcome they wanted, or None."""

    def __init__(self, field: str, outcome: dict[str, str], truth: Callable[[dict[str, Any]], str | None]):
        self.field = field
        self.outcome = outcome
        self.truth = truth

    def keys(self, raw):
        d = json_object(raw)
        return None if d is None else self.outcome.get(str(d.get(self.field)))

    def adjust(self, case, ref):
        return self.truth(case) or ref

    def score(self, ref, got):
        return 1.0 if ref == got else 0.0


def packet_fit_truth(case: dict[str, Any]) -> str | None:
    """The fit check leads to preparing on its own (fit) or asking first (doubt, no). What the user did
    says which was right: 要 to the question, or sending the packet, means prepare; 不要, or setting
    the packet aside, means it should not have been. The last word counts, and an undo takes back
    the one before it; undoing the preparing itself (nothing before it) says it should not have been.
    So a doubt the user answered 要 counts as a question that was not needed."""
    said: list[str] = []
    for f in case["feedback"]:
        kind = f.get("kind")
        if kind == "answer":
            said.append("prepare" if f.get("accepted") else "ask")
        elif kind in ("sent", "set_aside"):
            said.append("prepare" if kind == "sent" else "ask")
        elif kind == "undone":
            if said:
                said.pop()
            else:
                said.append("ask")
    return said[-1] if said else None


class PairsScorer:
    """F1 over (todo, same-as) pairs; the user's own word on a pair (feedback "same_task": todo, as, same) puts
    it in or takes it out."""

    def keys(self, raw):
        d = json_object(raw)
        if d is None:
            return None
        return {(str(x.get("todo")), str(x.get("as"))) for x in d.get("same") or [] if isinstance(x, dict)}

    def adjust(self, case, ref):
        for f in case["feedback"]:
            if f.get("kind") == "same_task":
                pair = (str(f.get("todo")), str(f.get("as")))
                ref = ref | {pair} if f.get("same") else ref - {pair}
        return ref

    def score(self, ref, got):
        return _f1(ref, got)


SCORERS: dict[str, Scorer] = {
    "todo_match": PairsScorer(),
    "route": RouteScorer(),
    "facts": FactsScorer(),
    "work_check": FieldScorer("domain"),
    "domain_classify": FieldScorer("domain"),
    "mail_triage": FieldScorer("category"),
    "window_classify": WindowScorer(),
    "packet_fit": LabelScorer("verdict", {"fit": "prepare", "doubt": "ask", "no": "ask"}, packet_fit_truth),
}  # any other task (answers, drafts, summaries, reflections) is graded by the judge


def users_word(case: dict[str, Any]) -> bool:
    """The case carries what the user said of it: an answer that settles its label, or a correction."""
    scorer = SCORERS.get(case["task"])
    return bool(isinstance(scorer, LabelScorer) and scorer.truth(case) is not None or case["feedback"])


def reference(case: dict[str, Any]) -> Any:
    """What a good reply should have said, or None when there is no trustworthy reference."""
    scorer = SCORERS.get(case["task"])
    if isinstance(scorer, LabelScorer) and (truth := scorer.truth(case)) is not None:
        return truth  # the user's own word, whoever answered the case
    if case["backend"] is None or case["by_local"]:
        return None  # only the cloud's replies serve as references (with the user's corrections)
    if any(f.get("kind") == "undone" for f in case["feedback"]):
        return None
    if scorer is None:
        return case["reply"]
    ref = scorer.keys(case["reply"])
    return None if ref is None else scorer.adjust(case, ref)


@dataclass
class TaskReport:
    task: str
    scored: int = 0
    skipped: int = 0
    total: float = 0.0
    misses: list[dict[str, Any]] = field(default_factory=list)
    ms: list[int] = field(default_factory=list)  # the candidate's time per case
    ref_ms: list[int] = field(default_factory=list)  # the time the recorded reply took

    @property
    def mean(self) -> float | None:
        return self.total / self.scored if self.scored else None

    @property
    def mean_ms(self) -> float | None:
        return sum(self.ms) / len(self.ms) if self.ms else None

    @property
    def ref_mean_ms(self) -> float | None:
        return sum(self.ref_ms) / len(self.ref_ms) if self.ref_ms else None


class Evaluator:
    def __init__(self, store: EvalStore, candidate: LLMBackend, judge: Judge | None = None,
                 on_case: Callable[[int, int], None] | None = None, timer: Callable[[], float] = time.monotonic):
        self._store = store
        self._candidate = candidate
        self._judge = judge
        self._on_case = on_case
        self._timer = timer

    def run(self, task: str | None = None, limit: int = 200) -> dict[str, TaskReport]:
        """Each task's score. A cloud candidate gets only the cases the cloud was allowed to read, credentials
        masked; the model that answered a case is scored on it only where the user's own word sets the reference
        (graded against its own reply, it would agree with itself)."""
        cases = [c for c in self._store.eval_cases(task, limit) if self._candidate.is_local or c["cloud_ok"]]
        reports: dict[str, TaskReport] = {}
        for n, case in enumerate(cases, 1):
            if self._on_case:
                self._on_case(n, len(cases))
            rep = reports.setdefault(case["task"], TaskReport(case["task"]))
            ref = reference(case)
            if ref is None or (case["backend"] == self._candidate.name and not users_word(case)):
                rep.skipped += 1
                continue
            prompt = request_text(case) if self._candidate.is_local else redact(request_text(case))[0]
            started = self._timer()
            try:
                got = self._candidate.complete(case["system"], prompt, json_mode=case["json_mode"])
            except Exception as e:  # a failing candidate scores zero on that case
                got = f"<error: {e}>"
            rep.ms.append(round((self._timer() - started) * 1000))
            if case.get("ms") is not None:
                rep.ref_ms.append(case["ms"])
            score = self._score(case, ref, got)
            if score is None:
                rep.skipped += 1
                continue
            rep.scored += 1
            rep.total += score
            if score < 1.0:
                rep.misses.append({"case": case["id"], "score": round(score, 2), "said": _said(case),
                                   "reference": _show(ref), "candidate": _show(self._keys(case, got))})
        return reports

    def _keys(self, case: dict[str, Any], raw: str | None) -> Any:
        scorer = SCORERS.get(case["task"])
        return scorer.keys(raw) if scorer else raw

    def _score(self, case: dict[str, Any], ref: Any, got: str | None) -> float | None:
        scorer = SCORERS.get(case["task"])
        if scorer is not None:
            keys = scorer.keys(got)
            return 0.0 if keys is None else scorer.score(ref, keys)
        if not got:
            return 0.0
        return self._judge.score(case, ref, got) if self._judge else None


def _said(case: dict[str, Any]) -> str:
    d = case["data"]
    return str(d.get("said") or d.get("fact") or d.get("question") or "")[:120]


def _show(x: Any) -> Any:
    if isinstance(x, set):
        return sorted(json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v for v in x)
    return x if not isinstance(x, str) else x[:400]


JUDGE_SYSTEM = """You grade an assistant's answer to its user's question against a reference answer written from the same material.
Reply with one JSON object and nothing else: {"score": <0-5>, "why": "<short>"}.
5: says the same things (wording may differ). 3: right gist, misses or blurs a point. 1: mostly wrong or invents things. 0: no answer."""


class ModelJudge:
    """Grades with the best model the case allows: the cloud for cases that
    were cleared for it when recorded, the local model otherwise."""

    def __init__(self, router: LLMRouter):
        self._router = router

    def score(self, case: dict[str, Any], reference: str, candidate: str) -> float | None:
        data = {"question": _said(case), "reference": reference, "candidate": candidate}
        payload = understanding_payload(data, Cleared(_said(case))) if case["cloud_ok"] else None
        req = (LLMRequest(JUDGE_SYSTEM, payload, Sensitivity.PERSONAL, UNDERSTANDING_DOMAIN, True, "judge") if payload else
               LLMRequest(JUDGE_SYSTEM, json.dumps(data, ensure_ascii=False), Sensitivity.SENSITIVE, None, True, "judge"))
        d = json_object(self._router.complete(req))
        try:
            return max(0.0, min(5.0, float(d["score"]))) / 5 if d else None
        except (KeyError, TypeError, ValueError):
            return None


def coverage(points: dict[str, dict[str, Any]], cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """How far each declared decision point is from being evaluable: the cases kept, how many carry
    the user's own word (feedback), and how it is scored. A point with no cases cannot be judged yet."""
    by_task: dict[str, list[dict[str, Any]]] = {}
    for c in cases:
        by_task.setdefault(c["task"], []).append(c)
    rows = []
    for name, p in points.items():
        kept = by_task.get(name, [])
        rows.append({"point": name, "kind": p.get("kind"), "scorer": (p.get("eval") or {}).get("scorer"),
                     "cases": len(kept), "with_feedback": sum(1 for c in kept if c.get("feedback")),
                     "by_local": sum(1 for c in kept if c.get("by_local"))})
    return rows


def decisions(points: dict[str, dict[str, Any]], cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For each decision point (kind: decide), how the recorded decisions fared: the cases kept, how many
    the user's own word settles (truth), how many of those the decision got right, and the time it took.
    Only a point with a label scorer can be settled by the user's word so far."""
    by_task: dict[str, list[dict[str, Any]]] = {}
    for c in cases:
        by_task.setdefault(c["task"], []).append(c)
    rows = []
    for name, p in points.items():
        if p.get("kind") != "decide":
            continue
        kept = by_task.get(name, [])
        scorer = SCORERS.get(name)
        settled = right = 0
        if isinstance(scorer, LabelScorer):
            for c in kept:
                truth = scorer.truth(c)
                if truth is None:
                    continue
                settled += 1
                label = (c.get("verdict") or {}).get("label")
                got = scorer.outcome.get(label) if label else scorer.keys(c["reply"])
                right += got == truth
        times = [c["ms"] for c in kept if c.get("ms") is not None]
        rows.append({"point": name, "cases": len(kept), "settled": settled,
                     "right": right if isinstance(scorer, LabelScorer) else None,
                     "accuracy": right / settled if settled else None,
                     "mean_ms": sum(times) / len(times) if times else None,
                     "by": sorted({str((c.get("verdict") or {}).get("by") or c["backend"]) for c in kept if c["backend"]})})
    return rows
