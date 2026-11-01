"""What a new fact from the user settles or changes, across every open item.

When the user tells Muninn something ("asked the builder: handover is 2027
Q3, the bank is X"), the local model compares it with everything still open:
the plans' deadlines, the trackers' next actions, and the to-dos other
projects keep. Each hit becomes either something Muninn can settle itself
(one tap) or a handoff to the project that owns the item. Work facts never
leave Muninn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from datetime import datetime
from typing import Any, Callable, Collection, Sequence

from . import plan_edits
from .handoffs import EFFECT_STRENGTH, Handoffs
from .models import DomainPlan
from .ports import Clock, ProjectRecords
from .open_items import OpenItems
from .privacy import Cleared
from .language import Language, written_in
from .reply_json import json_object

EFFECTS = ("resolves", "changes", "relevant")
SELF_PROJECT = "muninn"  # this app's own project: facts are already here
SHORTLIST = 25  # other projects' to-dos the model sees: the ones sharing the most words with the fact

SYSTEM = """The user just told their assistant a new fact. Compare it with the open items and say which ones it affects.
Reply with one JSON object and nothing else:
{"domain": "<domain id the fact is about, or null>",
 "effects": [{"id": "<item id, verbatim>", "effect": "resolves|changes|relevant", "why": "<one sentence, in {language}>",
              "new_due": "<YYYY-MM-DD if a date should move, else null>"}],
 "projects": ["<project names that should know this fact, from the given list>"],
 "summary": "<the fact restated in one sentence, in {language}>",
 "sure": <true when the user said it as settled; false when they only expect it, plan it or were told it>}
- The summary keeps how sure the user was: what they expect, plan or were told is said as expected, planned or heard.
- resolves: the fact shows the item is done or no longer needed. If the item asks the user to find out, ask, confirm or check something and the fact reports the answer, it is resolved, even when the answer moves other plans: say in why what now needs redoing.
- changes: the item still stands but its date, premise or next step should change.
- relevant: the item's owner should know, nothing to do yet.
- Each item at most once. Only items that the fact bears on."""


def system(words: Language) -> str:
    return written_in(SYSTEM, words)


@dataclass(frozen=True)
class Effect:
    id: str
    effect: str
    why: str
    new_due: str | None = None


@dataclass(frozen=True)
class Impact:
    domain: str | None
    summary: str
    effects: tuple[Effect, ...]
    projects: tuple[str, ...]
    case_id: int | None = None  # the evaluation case this judgment was recorded as


class ImpactAnalyzer:
    def __init__(self, open_items: OpenItems, understanding: Any, now: Callable[[], datetime],
                 domains: Callable[[bool], Sequence[tuple[str, str]]], projects: Callable[[], Sequence[str]],
                 work_projects: Callable[[], set[str]], *, words: Language):
        """`domains(cloud)`: (id, title) of the domains the model may see (no work domains for the cloud);
        `projects`: every project with records; `work_projects`: the ones in a work domain."""
        self.words = words
        self._open_items = open_items
        self._understanding = understanding
        self._now = now
        self._domains = domains
        self._projects = projects
        self._work = work_projects

    def open_items(self, cloud: bool = False) -> list[dict[str, Any]]:
        """Everything still open, each with a stable id; for the cloud under the privacy policy, nothing from work."""
        return [i.as_dict() for i in self._open_items.items(cloud)]

    def candidates(self, fact: str, cloud: bool = False) -> list[dict[str, Any]]:
        """Muninn's own items, plus the other projects' to-dos closest to the
        fact. A long list of unrelated to-dos makes the local model miss the
        obvious ones."""
        items = self.open_items(cloud)
        own = [i for i in items if not i["id"].startswith("todo:")]  # Muninn's own items and the registered tasks
        words = _terms(fact)
        scored = sorted(((len(words & _terms(i["text"])), n, i) for n, i in enumerate(items) if i["id"].startswith("todo:")),
                        key=lambda x: (-x[0], x[1]))
        return own + [i for score, _, i in scored[:SHORTLIST] if score > 0]

    def analyze(self, fact: str, cleared: Cleared | None = None) -> Impact | None:
        cloud = cleared is not None
        # the cloud model reads every open item; the local one only the closest
        items = self.open_items(cloud) if cloud else self.candidates(fact)
        projects = sorted({i["owner"] for i in self.open_items(cloud) if i["owner"] != "muninn"}
                          | (set(self._projects()) - (self._work() if cloud else set())))
        data = {
            "today": self._now().date().isoformat(),
            "domains": [{"id": d, "title": t} for d, t in self._domains(cloud)],
            "projects": projects,
            "open_items": items,
            "fact": fact,
        }
        c = self._understanding.complete("facts", system(self.words), data, cleared, json_mode=True)
        d, case = json_object(c.reply), c.case
        if d is None:
            return None
        known = {i["id"] for i in items}
        effects = []
        for x in d.get("effects") or []:
            if not isinstance(x, dict) or x.get("id") not in known or x.get("effect") not in EFFECTS:
                continue
            due = x.get("new_due")
            try:
                due = date.fromisoformat(due).isoformat() if due else None
            except ValueError:
                due = None
            effects.append(Effect(x["id"], x["effect"], str(x.get("why") or "")[:200], due))
        domain = d.get("domain") if d.get("domain") in {x["id"] for x in data["domains"]} else None
        # a restatement can sound surer than the user did: only what they said as settled is restated, in any language
        summary = str(d.get("summary") or fact)[:300] if d.get("sure") is True else fact
        return Impact(
            domain=domain,
            summary=summary,
            effects=tuple(effects[:6]),
            projects=tuple(p for p in d.get("projects") or [] if p in projects)[:4],
            case_id=case,
        )


class ImpactApplier:
    """Turns what a fact affects into one-tap questions (Muninn's own items,
    settled here and never handed off) and handoffs (other projects'
    to-dos, one per to-do). Work facts stay here."""

    def __init__(
        self,
        clock: Clock,
        records: ProjectRecords,
        handoffs: Handoffs,
        sensitive_domains: Collection[str],
        plan: Callable[[str], DomainPlan | None],
        mark_done: Callable[[str, str], Any],
        apply_edit: Callable[[str, Any], Any],
        tasks: Any = None, *, words: Language,
    ):
        """`plan` gives a domain's plan, unconfirmed items included. What the
        user reports is done, not asked about again: `mark_done(qualified
        deadline id, why)` and `apply_edit(domain, edit)` act and return the
        undoable record of it."""
        self.words = words
        self._clock = clock
        self._records = records
        self._handoffs = handoffs
        self._sensitive = sensitive_domains
        self._plan = plan
        self._mark_done = mark_done
        self._apply_edit = apply_edit
        self._tasks = tasks  # the TaskRegistry: a fact that settles a task closes it

    def apply(self, impact: Impact, quote: str | None = None, *, fyi: bool = True,
              only_to: Collection[str] | None = None) -> list[Any]:
        """`quote` is the user's own words, which the receiving project goes by.
        `fyi`: also tell the projects the fact is merely relevant to (no item
        of theirs affected); never for a fact Muninn could not place in a
        domain, which is how a passing remark reached four unrelated projects."""
        from .tools import Outcome

        # content from a lent session (only_to: its projects) is not passed to any other project
        words = self.words
        held = ((words("facts.held_from", projects=words.join(sorted(only_to))) if only_to else words("facts.held_lent"))
                if only_to is not None else "")

        def may_tell(project: str) -> bool:
            return only_to is None or project in only_to

        out = []
        work = impact.domain in self._sensitive
        todos = list(self._records.todos())
        told: set[str] = set()
        by_todo: dict[tuple[str, str], Effect] = {}
        for ef in impact.effects:
            kind, _, ref = ef.id.partition(":")
            if kind == "deadline":
                domain, _, did = ref.partition("/")
                plan = self._plan(domain)
                d = next((x for x in plan.deadlines if x.id == did), None) if plan else None
                if d is None:
                    continue
                if ef.effect == "resolves" and d.done_when.manual:
                    rec = self._mark_done(ref, ef.why)
                    out.append(Outcome(words("facts.marked_done", title=d.title, why=ef.why), action_id=rec.id))
                elif ef.effect == "changes" and ef.new_due:
                    rec = self._apply_edit(domain, plan_edits.SetDeadlineDue(did, ef.new_due))
                    out.append(Outcome(words("facts.why_after", text=rec.text, why=ef.why), action_id=rec.id))
                else:
                    out.append(Outcome(words("facts.about", title=d.title, why=ef.why)))
            elif kind == "task" and self._tasks is not None:
                t = self._tasks.get(ref)
                if t is None or t["status"] != "open":
                    continue
                if ef.effect == "resolves":
                    # the user's words settled it, but which task they settle is the model's reading of them: closed on that
                    # evidence (undoably), not as the user's own decision. Projects outside only_to hear that it is done, not
                    # the why written from the lent content.
                    rec = self._tasks.close(ref, by="evidence", why=ef.why, only_to=only_to)
                    out.append(Outcome(words("facts.task_done", n=t["n"], title=t["title"][:40], why=ef.why), action_id=rec.id if rec else None))
                elif ef.effect == "changes" and ef.new_due:
                    rec = self._tasks.set_due(ref, ef.new_due)
                    out.append(Outcome(words("facts.task_moved", n=t["n"], due=ef.new_due, why=ef.why), action_id=rec.id if rec else None))
                else:
                    out.append(Outcome(words("facts.task_about", n=t["n"], title=t["title"][:40], why=ef.why)))
            elif kind == "tracker":
                out.append(Outcome(f"{ef.why}"))
            elif kind == "todo":
                project = ref.split(":", 1)[0]
                todo = next((t.text for n, t in enumerate(todos) if f"{t.project}:{n}" == ref), "")
                prev = by_todo.get((project, todo))
                if prev is None or EFFECT_STRENGTH[ef.effect] > EFFECT_STRENGTH[prev.effect]:
                    by_todo[(project, todo)] = ef  # one verdict per to-do: the strongest
        for (project, todo), ef in by_todo.items():
            if work:
                out.append(Outcome(words("facts.todo_work", project=project, todo=todo[:40], why=ef.why)))
                continue
            if not may_tell(project):
                out.append(Outcome(words("facts.todo_held", project=project, todo=todo[:40], held=held)))
                continue
            h = self._handoffs.add(project=project, fact=impact.summary, quote=quote, item=todo, effect=ef.effect, why=ef.why)
            said = {"resolves": words("facts.todo_resolves", project=project, todo=todo[:40], why=ef.why),
                    "changes": words("facts.todo_changes", project=project, todo=todo[:40], why=ef.why),
                    "relevant": words("facts.todo_relevant", project=project, todo=todo[:40], why=ef.why)}[ef.effect]
            out.append(Outcome(said, handoff_id=h["id"]))
            told.add(project)
        if not work:
            for project in (impact.projects if fyi and impact.domain is not None else ()):
                if project not in told and project != SELF_PROJECT and may_tell(project):
                    h = self._handoffs.add(project=project, fact=impact.summary, quote=quote, item=None, effect="relevant", why="")
                    out.append(Outcome(words("facts.will_tell", project=project), handoff_id=h["id"]))
                    told.add(project)
        return out


def _terms(text: str) -> set[str]:
    """Words for matching: Latin words and digits, and pairs of adjacent CJK characters."""
    text = text.lower()
    latin = set(re.findall(r"[a-z0-9]{2,}", text))
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    return latin | {run[i : i + 2] for run in cjk for i in range(len(run) - 1)}
