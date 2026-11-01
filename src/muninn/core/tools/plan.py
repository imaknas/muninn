"""Tools that change a domain's plan: an edit the user asks for, a decision on a suggested item,
and a new domain drafted from what the user said."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Callable, ClassVar, Sequence

from ..actions import ActionGate
from ..language import Language
from ..models import ActionRecord, DomainPlan, Goal
from ..plan_edits import Refused, from_dict
from ..ports import Clock
from ..progress import GoalProgress
from . import Outcome, Turn
from .records import LogHours, MarkDone, Progress


class ChangePlan:
    name: ClassVar[str] = "change_plan"
    description: ClassVar[str] = ("The user wants to change the plan. args: {domain: <domain id>, op: <goal_target | deadline_due | add_target | remove_target | remove_entry>, "
                                  "goal: <goal id, for goal_target>, target: <number, for goal_target>, deadline: <deadline id, for deadline_due>, "
                                  "due: <YYYY-MM-DD, for deadline_due>, id: <new or existing target id, for add_target/remove_target>, "
                                  "company: <for add_target>, role: <for add_target>, priority: <1-3, for add_target>, url: <optional>, "
                                  "section: <goals | deadlines, for remove_entry (removing a goal or a deadline; id is its id)>}")
    lookup: ClassVar[bool] = False

    def __init__(self, plan: Callable[[str], DomainPlan | None], apply_edit: Callable[[str, Any], Any]):
        """`plan`: a domain's plan, unconfirmed items included; `apply_edit(domain, edit)` changes it
        (the user's words are the decision: done now, undoable) and returns the record."""
        self._plan = plan
        self._apply = apply_edit

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        domain = str(a.pop("domain", ""))
        # the model sometimes writes qualified ids ("jobs/golang") where a bare id belongs
        domain, _, rest = domain.partition("/")
        for key in ("goal", "deadline", "id"):
            if isinstance(a.get(key), str) and "/" in a[key]:
                a[key] = a[key].split("/", 1)[1]
        if rest:
            slot = {"goal_target": "goal", "deadline_due": "deadline", "remove_target": "id"}.get(str(a.get("op")))
            if slot and not a.get(slot):
                a[slot] = rest
        plan = self._plan(domain)
        if plan is None:
            raise ValueError(turn.words("tools_plan.no_domain", domain=domain))
        if a.get("op") == "add_target" and not a.get("next_action"):
            a["next_action"] = turn.words("tools_plan.apply")  # a new target's first step, as the tracker shows it
        try:
            edit = from_dict({k: v for k, v in a.items() if v is not None and not k.startswith("_")})
        except TypeError:  # the model left out what to change or to what: ask, don't guess
            raise ValueError(turn.words("tools_plan.what_change")) from None
        except Refused as e:
            raise ValueError(e.say(turn.words)) from None
        # the line names exactly what changed
        rec = self._apply(domain, edit)
        return Outcome(f"[{plan.title}] {rec.text}", action_id=rec.id)


class DecideItem:
    name: ClassVar[str] = "decide_item"
    description: ClassVar[str] = "The user plainly takes a position on the item under discussion (about, whose kind is review). args: {decision: confirm | reject | change, value: <the new number or YYYY-MM-DD, for change>}"
    lookup: ClassVar[bool] = False

    def __init__(self, review: Callable[[str, str, Any], ActionRecord]):
        """`review(review id, decision, value)`: the plan review's decision, undoable."""
        self._review = review

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        about = turn.about or {}
        if about.get("kind") != "review":
            raise ValueError(turn.words("tools_plan.no_item"))
        decision = str(a.get("decision"))
        if decision == "change" and a.get("value") in (None, ""):
            raise ValueError(turn.words("tools_plan.change_to"))
        rec = self._review(about["ref"], decision, a.get("value"))
        return Outcome(rec.text, action_id=rec.id)


class PlanTools:
    """The plans' record keeping tools: the goals' progress, hours, a deadline done (changing a
    plan is the Plans actor's)."""

    def __init__(self, plan: Callable[[str], DomainPlan | None], plans: Callable[[], Sequence[DomainPlan]],
                 progress: Callable[[DomainPlan, Goal, datetime], GoalProgress | None], gate: ActionGate, clock: Clock):
        """`plan`: a domain's plan, unconfirmed items included; `plans`: the confirmed plans; hours and
        deadlines done go through the gate."""
        self._plan = plan
        self._plans = plans
        self._progress = progress
        self._gate = gate
        self._clock = clock

    def tools(self) -> list[Any]:
        return [LogHours(self._gate), MarkDone(self._plan, self._gate), Progress(self._plans, self._progress, self._clock)]


class ProposeDomain:
    name: ClassVar[str] = "propose_domain"
    description: ClassVar[str] = ("The user wants to open something as a new domain: kept up over the long term, with goals of its own (e.g. turning the idea under discussion into a proper plan). "
                                  "args: {id: <lowercase English, digits, hyphens>, title: <its name, in the words the user said>, hours_per_week: <how many hours a week the user said the user wants to spend; null if the user did not say>, "
                                  "deadlines: [{title: <what is to be achieved>, due: <YYYY-MM-DD>}] (only ones the user said), why: <what the user said it rests on, one sentence>}")
    lookup: ClassVar[bool] = False
    acts_on: ClassVar[tuple[str, ...]] = ("idea",)  # an idea discussed may grow into one

    def __init__(self, create: Callable[[dict[str, Any], str], Any], week_hours: Callable[[], Sequence[tuple[str, float]]],
                 today: Callable[[], date], tz: str):
        """`create(doc, source)` makes the domain (the user's words: at once, undoable); `week_hours`:
        the weekly hours goals there already are (title, hours)."""
        self._create = create
        self._week_hours = week_hours
        self._today = today
        self._tz = tz

    def run(self, a: dict[str, Any], turn: Turn) -> Outcome:
        did, title = str(a.get("id") or "").strip().lower(), str(a.get("title") or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", did) or not title:
            raise ValueError(turn.words("tools_plan.needs_name"))
        about = turn.about or {}
        source = f"idea:{about['ref']}" if about.get("kind") == "idea" else ""
        # what each drafted item is based on: the idea it grew out of, or this conversation
        basis = (f"{source}:" if source else turn.words("tools_plan.from_talk", day=self._today().isoformat())) + str(a.get("why") or title)[:160]
        hours = _number(a.get("hours_per_week"))
        goals = [{"id": "weekly-hours", "title": turn.words("tools_plan.weekly_hours", title=title), "metric": "hours", "target": hours, "period": "week",
                  "params": {"source": basis}}] if hours else []
        deadlines = []
        for i, d in enumerate(a.get("deadlines") or [], 1):
            try:
                due = date.fromisoformat(str((d or {}).get("due"))).isoformat()
            except ValueError:
                raise ValueError(turn.words("tools_plan.bad_due", title=(d or {}).get("title"))) from None
            deadlines.append({"id": f"deadline-{i}", "title": str(d.get("title") or "")[:120], "due": due, "source": basis})
        doc = {"domain": {"id": did, "title": title, "priority": 3}, "timezone": self._tz, "goals": goals, "deadlines": deadlines}
        r = self._create(doc, source)
        if r is None:
            raise ValueError(turn.words("tools_plan.not_made"))
        if r.confirm_id:
            return Outcome(r.text, confirm_id=r.confirm_id)
        return Outcome(r.text + self._budget(hours, turn.words), action_id=r.id)

    def _budget(self, hours: float | None, words: Language) -> str:
        """What the new hours compete with: the weekly hours already asked of the week."""
        now = list(self._week_hours())
        if not hours:
            return words("tools_plan.budget_none")
        if not now:
            return words("tools_plan.budget_alone", hours=f"{hours:g}")
        total = sum(h for _, h in now)
        listed = words.join(words("tools_plan.budget_item", title=t, hours=f"{h:g}") for t, h in now)
        return words("tools_plan.budget_added", hours=f"{hours:g}", listed=listed, total=f"{total + hours:g}")


def _number(x: Any) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None
