"""Answering the user's questions about their own plans and progress.

The answer is looked up where it lives: the domain plans (goals and why they
exist, targets and their notes, deadlines, pivot, decision tree, filters),
the trackers, current progress, and the state summaries of the projects that
belong to each domain. That is the user's most private material: it goes to
the cloud only for a question the work check cleared, and then without the
work domains (see understanding.py).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from .llm_router import Completion
from .models import Attachment, DomainPlan, Goal
from .privacy import Cleared
from .progress import GoalProgress
from .language import Language, written_in

CONTEXT_CHARS = 24_000
STATE_CHARS = 5_000

SYSTEM = """You are Muninn, the user's butler. Answer the user's question in {language}, conversationally and briefly (usually 2 to 5 sentences).
Answer only from the data in context, and say where the answer rests. When an item has a source field, go by it: a source that begins by saying it was inferred was worked out while planning, not said by the user, and must be told as such (e.g. "The plan's note on Netskope AI Gateway says…").
When the data holds no answer, say so plainly, and suggest what the user could add.
On investments and medical matters, say only what the data holds; give no advice."""


def system(words: Language) -> str:
    return written_in(SYSTEM, words)


def plan_digest(plan: DomainPlan) -> dict[str, Any]:
    return {
        "domain": plan.id,
        "title": plan.title,
        "priority": plan.priority,
        "goals": [{"id": g.id, "title": g.title, "target": g.target, "period": g.period, "metric": g.metric,
                   "if_behind": list(g.if_behind), "source": g.params.get("source")} for g in plan.goals],
        "deadlines": [{"id": d.id, "title": d.title, "due": d.due.isoformat(), "source": d.source} for d in plan.deadlines],
        "targets": [{"id": t.id, "title": t.title, "priority": t.priority, "notes": t.notes} for t in plan.targets],
        "tracker": [{"target": i.target, "status": i.status, "next_action": i.next_action,
                     "due": i.next_action_due.isoformat() if i.next_action_due else None} for i in plan.tracker],
        "pivot": plan.pivot,
        "decision_tree": list(plan.decision_tree),
        "filters": list(plan.filters),
    }


def projects_by_domain(configured: Mapping[str, Any], learned: Mapping[str, Any]) -> dict[str, list[str]]:
    """Projects whose activity the attribution rules put in each domain: the configured
    repos (attribution.yaml "repos") and the ones the user confirmed ("repo:<name>" rules)."""
    out: dict[str, list[str]] = {}
    learned_repos = ((k.split(":", 1)[1], v) for k, v in learned.items() if k.startswith("repo:"))
    for repo, v in [*configured.items(), *learned_repos]:
        domain = v if isinstance(v, str) else (v or {}).get("domain")
        if domain:
            out.setdefault(domain, []).append(str(repo))
    return out


class Answerer:
    def __init__(self, now: Callable[[], datetime], understanding: Any,
                 plans: Callable[[bool], Sequence[DomainPlan]], work_projects: Callable[[], set[str]],
                 progress: Callable[[DomainPlan, Goal, datetime], GoalProgress | None],
                 projects: Callable[[], dict[str, list[str]]], project_state: Callable[[str], str | None],
                 calendar: Callable[[datetime, datetime, bool], Any], *, words: Language):
        """`plans(cloud)`: every plan, without the work domains for the cloud; `progress`: a goal's
        progress now; `projects`: projects per domain; `project_state`: a project's state summary;
        `calendar(start, end, cloud)`: the calendar as the model may see it."""
        self.words = words
        self._now = now
        self._understanding = understanding
        self._plans = plans
        self._work = work_projects
        self._progress = progress
        self._projects = projects
        self._project_state = project_state
        self._calendar = calendar

    def context(self, cloud: bool = False) -> dict[str, Any]:
        from .reports import goal_line

        now = self._now()
        plans = sorted(self._plans(cloud), key=lambda p: p.priority)  # unconfirmed suggestions too
        work = self._work() if cloud else set()
        progress = [goal_line(p, self.words)[2:] for plan in plans for g in plan.goals if (p := self._progress(plan, g, now))]
        states: dict[str, str] = {}
        by_domain = self._projects()
        for plan in plans:
            for project in by_domain.get(plan.id, []):
                if project not in work and (st := self._project_state(project)):
                    states[project] = st[:STATE_CHARS]
        ctx = {"today": now.date().isoformat(), "plans": [plan_digest(p) for p in plans if p.goals or p.deadlines or p.targets],
               "progress": progress, "calendar": self._calendar(now - timedelta(days=1), now + timedelta(days=14), cloud),
               "project_states": states}
        while len(json.dumps(ctx, ensure_ascii=False, default=str)) > CONTEXT_CHARS and ctx["project_states"]:
            ctx["project_states"].pop(next(iter(reversed(ctx["project_states"]))))
        return ctx

    def answer(self, question: str, cleared: Cleared | None = None, task: str = "answer") -> str | None:
        return self.reply(question, cleared, task).reply

    def reply(self, question: str, cleared: Cleared | None = None, task: str = "answer",
              attachments: Sequence[Attachment] = ()) -> Completion:
        """The answer as the model call came back: who wrote it, or that nothing could. `attachments`: the files
        the user sent with the question, looked at too."""
        data = {"context": self.context(cloud=cleared is not None), "question": question}
        c = self._understanding.complete(task, system(self.words), data, cleared, attachments=attachments)
        return replace(c, reply=c.reply.strip() if c.reply else None)
