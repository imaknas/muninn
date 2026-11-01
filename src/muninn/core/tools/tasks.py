"""Tools for what only the user can do: add a task, close it, move its date."""

from __future__ import annotations

from typing import Any, ClassVar

from ..tasks import TaskRegistry
from . import Outcome, Turn


def task_key(tasks: TaskRegistry, a: dict[str, Any], turn: Turn) -> str:
    """The task named, or the one being discussed."""
    key = str(a.get("task") or "") or ((turn.about or {}).get("ref") if (turn.about or {}).get("kind") == "task" else "")
    if not key or tasks.get(str(key)) is None:
        raise ValueError(turn.words("tools_tasks.which"))
    return str(key)


class AddTask:
    name: ClassVar[str] = "add_task"
    description: ClassVar[str] = "The user wants to note a concrete thing only the user can do, finished once done (e.g. \"remind me to ask the estate agent…\"). args: {title: <the thing>, due: <YYYY-MM-DD or null>}"
    lookup: ClassVar[bool] = False

    def __init__(self, tasks: TaskRegistry):
        self._tasks = tasks

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        t = self._tasks.add(str(a["title"]), a.get("due"))
        return Outcome(turn.words("tools_tasks.added_due", n=t["n"], title=t["title"], due=t["due"]) if t["due"]
                       else turn.words("tools_tasks.added", n=t["n"], title=t["title"]))


class CloseTask:
    name: ClassVar[str] = "close_task"
    description: ClassVar[str] = "A task is done or no longer needed. args: {task: <task key or T-n; may be left out for the one under discussion>, status: <done | dropped>, why: <one sentence>}"
    lookup: ClassVar[bool] = False

    def __init__(self, tasks: TaskRegistry):
        self._tasks = tasks

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        key = task_key(self._tasks, a, turn)
        rec = self._tasks.close(key, "dropped" if a.get("status") == "dropped" else "done", why=str(a.get("why") or ""),
                                only_to=turn.only_to)
        if rec is None:
            raise ValueError(turn.words("tools_tasks.already_closed"))
        return Outcome(rec.text, action_id=rec.id)


class SetTaskDue:
    name: ClassVar[str] = "set_task_due"
    description: ClassVar[str] = "Change a task's date. args: {task: <task key or T-n; may be left out for the one under discussion>, due: <YYYY-MM-DD>}"
    lookup: ClassVar[bool] = False

    def __init__(self, tasks: TaskRegistry):
        self._tasks = tasks

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        rec = self._tasks.set_due(task_key(self._tasks, a, turn), str(a.get("due")))
        if rec is None:
            raise ValueError(turn.words("tools_tasks.bad_date"))
        return Outcome(rec.text, action_id=rec.id)
