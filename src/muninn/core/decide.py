"""Decisions: a decision point picks one of a fixed set of answers (its labels in
contracts/llm-points.yaml), with how sure it is and who decided.

The core asks a Decider and never knows which model stands behind it. Today
that is the general model through the router (LLMDecider); a model that only
returns probabilities can stand in for a point once its evaluation shows it
is as good there. Each point's wording stays with the code that asks it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .llm_router import CloudSafe, LLMRequest, LLMRouter
from .models import Sensitivity
from .reply_json import json_object


@dataclass(frozen=True)
class Question:
    point: str  # the decision point, as declared in the registry
    system: str
    content: str | CloudSafe
    labels: tuple[str, ...]
    sensitivity: Sensitivity
    domain: str | None
    field: str = "label"  # where the general model's JSON reply puts the answer
    keep: tuple[str, ...] = ()  # other fields of the reply the asker uses (a decision-only model leaves them empty)


@dataclass(frozen=True)
class Verdict:
    label: str
    confidence: float | None  # 0..1; None when the decider cannot say (a general model's own word is not a measure)
    by: str  # who decided: the backend's name
    why: str = ""
    case: int | None = None  # the evaluation case it was kept as
    fields: dict[str, Any] = field(default_factory=dict)  # the Question's `keep` fields, as answered


class Decider(Protocol):
    def decide(self, q: Question) -> Verdict | None:
        """None when nothing could decide now (no model reachable, or an answer outside the labels)."""
        ...


class LLMDecider:
    """The general model, through the router (its routing and privacy rules), asked to reply with JSON."""

    def __init__(self, router: LLMRouter, keep: Callable[[int, dict[str, Any]], None] | None = None):
        """`keep(case, {label, confidence, by})`: keeps the verdict on the evaluation case the request was recorded as."""
        self._router = router
        self._keep = keep or (lambda case, verdict: None)

    def decide(self, q: Question) -> Verdict | None:
        c = self._router.complete_case(LLMRequest(q.system, q.content, q.sensitivity, q.domain, True, q.point))
        data = json_object(c.reply)
        if data is None or c.backend is None or data.get(q.field) not in q.labels:
            return None
        verdict = Verdict(str(data[q.field]), None, c.backend, str(data.get("why") or "").strip()[:300], c.case,
                          {k: data.get(k) for k in q.keep})
        if c.case is not None:
            self._keep(c.case, {"label": verdict.label, "confidence": verdict.confidence, "by": verdict.by})
        return verdict
