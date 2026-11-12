"""Which cloud model reads each kind of request.

Every LLM request names its task (LLMRequest.task). The tasks the user picks a
model for are grouped here (the conversation's routing, what news settles,
answers, drafts, mail, reflection); config `llm.models` names a provider and
model per group, and a group it leaves out keeps its default. Config
`llm.points` names one for a single decision point (contracts/llm-points.yaml),
over its group's. Tasks in neither use the router's own cloud model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .language import Language


@dataclass(frozen=True, order=True)
class ModelChoice:
    provider: str  # the provider's name in config llm.providers
    model: str


@dataclass(frozen=True)
class TaskGroup:
    key: str  # its name in config llm.models (and what the settings page names it by: described)
    tasks: tuple[str, ...]
    tier: str  # how strong a model it gets unless config says otherwise: standard for the user's own words, fast for the rest


GROUPS = (
    TaskGroup("route", ("route",), "standard"),
    TaskGroup("facts", ("facts",), "standard"),
    TaskGroup("answer", ("answer", "why", "mail_answer"), "standard"),
    TaskGroup("draft", ("packet", "packet_fit", "form_fill"), "fast"),
    TaskGroup("mail", ("mail_triage",), "fast"),
    TaskGroup("reflect", ("reflect_digest", "reflect_week"), "fast"),
)


class TaskModels:
    def __init__(self, defaults: Mapping[str, ModelChoice], configured: Mapping[str, ModelChoice | None] | None = None,
                 points: Mapping[str, ModelChoice] | None = None):
        """`defaults`: group key → the model it uses unless configured; `configured`: what config llm.models chose;
        `points`: decision point → the model config llm.points chose for it alone."""
        configured = dict(configured or {})
        keys = {g.key for g in GROUPS}
        if unknown := set(configured) - keys:
            raise ValueError(f"llm.models: unknown {sorted(unknown)}; known: {sorted(keys)}")
        self._configured = {k: v for k, v in configured.items() if v}
        self._defaults = dict(defaults)
        self._group = {t: g.key for g in GROUPS for t in g.tasks}
        self._points = dict(points or {})

    def model(self, task: str) -> ModelChoice | None:
        """The model for this task; None for a task in no group that config names no model for."""
        if task in self._points:
            return self._points[task]
        key = self._group.get(task)
        return None if key is None else self._configured.get(key) or self._defaults[key]

    def points(self) -> dict[str, ModelChoice]:
        return dict(self._points)

    def table(self) -> list[dict[str, Any]]:
        """Each group with the model it uses now, and whether config chose it."""
        return [{"key": g.key, "model": self._configured.get(g.key, self._defaults[g.key]).model,
                 "configured": g.key in self._configured} for g in GROUPS]


def described(table: list[dict[str, Any]], words: Language) -> list[dict[str, Any]]:
    """The table with each group's name and what it covers, as the settings page shows them."""
    return [{**r, "label": words("task_models.label", group=r["key"]), "detail": words("task_models.detail", group=r["key"])}
            for r in table]
