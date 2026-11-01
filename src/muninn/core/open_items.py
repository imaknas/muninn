"""Everything still open, from wherever it is kept, through one interface.

Each kind of item keeps its own owner: the plans keep deadlines, the
trackers keep next actions, the task registry keeps what only the user can
do, and the projects keep their own to-dos (minus the lines that are
registered tasks, marked 「等 <owner>:<key>」). Facts, the home page and the
duplicate matcher read them all here; every item has a stable id whose
prefix names its source (deadline: tracker: task: todo:).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol, Sequence

from .models import DomainPlan

def registered_marker(owner: str) -> Callable[[str], bool]:
    """How a project's line points at a registered task: 「(等 <owner>:task-…)」,
    with the user's label as the records system writes it, colon ASCII or full-width."""
    pattern = re.compile(rf"等\s*{re.escape(owner)}\s*[:：]")
    return lambda text: bool(pattern.search(text))


@dataclass(frozen=True)
class OpenItem:
    id: str
    owner: str  # "muninn" for Muninn's own, else the project
    text: str
    due: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        extra = d.pop("extra")
        return {k: v for k, v in {**d, **extra}.items() if v is not None}


class ItemSource(Protocol):
    def items(self, cloud: bool) -> list[OpenItem]:
        """`cloud`: the items may be read by the cloud model under the privacy-first policy (no work)."""
        ...


class DeadlineItems:
    def __init__(self, plans: Callable[[bool], Sequence[DomainPlan]], completed: Callable[[], set[str]],
                 decisions: Callable[[], dict[str, str]]):
        """`plans(cloud)`: every plan, without the work domains for the cloud; `completed`: deadlines
        marked done; `decisions`: the plan review's decisions (a rejected deadline is not open)."""
        self._plans = plans
        self._completed = completed
        self._decisions = decisions

    def items(self, cloud):
        completed, decided = self._completed(), self._decisions()
        return [OpenItem(f"deadline:{p.id}/{d.id}", "muninn", d.title, d.due.isoformat(), {"manual": d.done_when.manual})
                for p in self._plans(cloud) for d in p.deadlines
                if f"{p.id}/{d.id}" not in completed and decided.get(f"{p.id}/deadline:{d.id}") != "rejected"]


class TrackerItems:
    def __init__(self, plans: Callable[[bool], Sequence[DomainPlan]]):
        self._plans = plans

    def items(self, cloud):
        return [OpenItem(f"tracker:{p.id}/{i.target}", "muninn", f"{p.target_title(i.target)}:{i.next_action}",
                         i.next_action_due.isoformat() if i.next_action_due else None)
                for p in self._plans(cloud) for i in p.tracker if not i.is_closed and i.next_action]


class TaskItems:
    def __init__(self, open_tasks: Callable[[], Sequence[dict[str, Any]]], work_projects: Callable[[], set[str]]):
        """`work_projects`: projects in a work domain, whose tasks the cloud never reads."""
        self._open = open_tasks
        self._work = work_projects

    def items(self, cloud):
        work = self._work() if cloud else set()
        out = []
        for t in self._open():
            projects = [o["project"] for o in t["origins"]]
            if work & set(projects):
                continue
            out.append(OpenItem(f"task:{t['key']}", projects[0] if projects else "muninn", t["title"], t["due"],
                                {"n": t["n"], "projects": projects}))
        return out


class ProjectTodoItems:
    def __init__(self, todos: Callable[[], Sequence[Any]], registered: Callable[[str], bool],
                 work_projects: Callable[[], set[str]], is_work_line: Callable[[str], bool]):
        """`todos`: the projects' to-do lines; `registered`: whether a line points at a registered task;
        `is_work_line`: whether a line reads as work (kept from the cloud)."""
        self._todos = todos
        self._registered = registered
        self._work = work_projects
        self._is_work_line = is_work_line

    def items(self, cloud):
        work = self._work() if cloud else set()
        return [OpenItem(f"todo:{t.project}:{n}", t.project, t.text, None, {"who": t.owner})
                for n, t in enumerate(self._todos())
                if not self._registered(t.text) and t.project not in work
                and not (cloud and self._is_work_line(t.text))]


class OpenItems:
    def __init__(self, sources: Sequence[ItemSource]):
        self._sources = list(sources)

    def items(self, cloud: bool = False, kinds: Callable[[str], bool] = lambda k: True) -> list[OpenItem]:
        return [i for s in self._sources for i in s.items(cloud) if kinds(i.id.split(":", 1)[0])]
