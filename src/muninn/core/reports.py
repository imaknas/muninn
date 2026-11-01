"""Deterministic text for the morning brief, evening summary and weekly report.

Each report is one inbox item covering every domain, highest priority first,
so a push is "muninn: 1 new reminder" rather than one per domain."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping, Sequence

from .drift import DEFAULT_THRESHOLDS, Threshold, fmt_amount
from .language import Language
from .models import InboxItem
from .planner import DomainAgenda, Priority
from .privacy import DayFacts
from .progress import GoalProgress

REPORT_DOMAIN = "muninn"


def goal_line(p: GoalProgress, words: Language) -> str:
    """A goal's progress in one line, after a one-character mark and a space (callers that want the words alone
    cut those two off)."""
    if not p.known:
        return "· " + words("reports.goal_unknown", goal=p.goal.title)
    mark = "✓" if p.met else ("⚠" if p.deficit > 0 else "·")
    a, t = fmt_amount(p.actual or 0.0, p.unit, words), fmt_amount(p.target, p.unit, words)
    if p.direction == "at_most":
        return f"{mark} " + words("reports.goal_at_most", goal=p.goal.title, actual=a, target=t)
    pace = words("reports.pace", expected=fmt_amount(p.expected, p.unit, words)) if p.expected < p.target else ""
    if p.goal.period == "week":
        return f"{mark} " + words("reports.goal_this_week", goal=p.goal.title, actual=a, target=t, pace=pace)
    return f"{mark} " + words("reports.goal_today", goal=p.goal.title, actual=a, target=t, pace=pace)


def goal_state(p: GoalProgress, words: Language) -> tuple[str, str]:
    """(state, short label): unknown | done | over | behind | pace."""
    if not p.known:
        return "unknown", words("reports.no_data")
    if p.direction == "at_most":
        return ("done", words("reports.kept")) if p.met else ("over", words("reports.over"))
    if p.met:
        return "done", words("reports.met")
    if DEFAULT_THRESHOLDS.get(p.unit, Threshold(1, 1)).level(p.deficit) > 0:
        return "behind", words("reports.behind", amount=fmt_amount(p.deficit, p.unit, words))
    return "pace", words("reports.on_pace")


def goal_value(p: GoalProgress, words: Language) -> str:
    if not p.known:
        return "—"
    a = fmt_amount(p.actual or 0.0, p.unit, words)
    if p.direction == "at_most":
        return a
    return f"{a} / {fmt_amount(p.target, p.unit, words)}"


def goal_data(p: GoalProgress, words: Language) -> dict[str, Any]:
    state, label = goal_state(p, words)
    return {
        "state": state,
        "state_label": label,
        "value": goal_value(p, words),
        "id": p.goal.id,
        "title": p.goal.title,
        "period": p.goal.period,
        "actual": None if p.actual is None else round(p.actual, 2),
        "target": p.target,
        "expected": round(p.expected, 2),
        "unit": p.unit,
        "direction": p.direction,
        "met": p.met,
        "behind": p.deficit > 0,
        "line": goal_line(p, words),
    }


def _priority_data(p: Priority, domain: str) -> dict[str, Any]:
    return {"text": p.text, "why": p.why, "kind": p.kind, "target": p.target, "domain": domain, "priority": p.priority}


def agenda_data(a: DomainAgenda, words: Language) -> dict[str, Any]:
    return {
        "domain": a.plan.id,
        "title": a.plan.title,
        "priority": a.plan.priority,
        "observe_only": a.plan.observe_only,
        "filters": list(a.plan.filters),
        "today": [_priority_data(p, a.plan.id) for p in a.today],
        "week": [_priority_data(p, a.plan.id) for p in a.week],
        "goals": [goal_data(p, words) for p in a.goals],
        "deadlines": [
            {
                "id": s.deadline.id,
                "title": s.deadline.title,
                "due": s.deadline.due.isoformat(),
                "days_left": s.days_left,
                "manual": s.deadline.done_when.manual,
                "pending": list(s.pending_targets),
                "in_lead_window": s.in_lead_window,
            }
            for s in a.deadlines
        ],
    }


def top_priorities(agendas: Sequence[DomainAgenda], n: int = 5) -> list[tuple[DomainAgenda, Priority]]:
    """When domains compete for the same day, the higher-priority domain wins."""
    pairs = [(a, p) for a in agendas for p in a.today]
    pairs.sort(key=lambda ap: (ap[0].plan.priority, ap[1].priority))
    return pairs[:n]


def _has_content(a: DomainAgenda) -> bool:
    return bool(a.today or a.week or a.goals or a.deadlines)


def _priority_lines(top: Sequence[tuple[DomainAgenda, Priority]], words: Language) -> list[str]:
    return [words("reports.priority", n=i, domain=a.plan.title, text=p.text, why=p.why) for i, (a, p) in enumerate(top, 1)]


def morning_brief(agendas: Sequence[DomainAgenda], day: date, now: datetime, project_todos: int = 0, *,
                  words: Language) -> InboxItem:
    agendas = sorted((a for a in agendas if _has_content(a)), key=lambda a: a.plan.priority)
    top = top_priorities(agendas)
    lines = [words("reports.do_first")] if top else [words("reports.nothing_due")]
    lines += _priority_lines(top, words)
    if project_todos:
        lines += ["", words("reports.project_todos", n=project_todos)]
    for a in agendas:
        section = [goal_line(p, words) for p in a.goals] + [words("reports.week_item", text=p.text, why=p.why) for p in a.week]
        if section:
            lines += ["", words("reports.section", title=a.plan.title)] + section
    return InboxItem(
        id=None,
        ts=now,
        domain=REPORT_DOMAIN,
        kind="brief",
        title=words("reports.brief_title", day=day),
        body="\n".join(lines),
        data={"top": [_priority_data(p, a.plan.id) for a, p in top], "domains": [agenda_data(a, words) for a in agendas],
              "push_title": words("reports.push_first", text=top[0][1].text) if top else words("reports.push_nothing")},
    )


def evening_summary(
    facts: Sequence[DayFacts],
    tomorrow: Sequence[DomainAgenda],
    wording: Mapping[str, str | None],
    digest: Mapping[str, Sequence[str]],
    day: date,
    now: datetime,
    places_line: str | None = None,
    *,
    words: Language,
) -> InboxItem:
    """`digest`: titles of today's alerts that were kept out of push, per domain."""
    lines: list[str] = [places_line] if places_line else []
    sections = []
    for f in sorted(facts, key=lambda f: f.plan.priority):
        plan = f.plan
        sec: list[str] = []
        if wording.get(plan.id):
            sec.append(wording[plan.id].strip())
        if f.applied_today:
            sec.append(words("reports.applied", targets=words.join(plan.target_title(t) for t in f.applied_today)))
        for p in f.goals:
            if p.unit == "h" and p.goal.period == "week":
                sec.append(words("reports.week_hours", goal=p.goal.title, today=f.today_by_goal.get(p.goal.id, 0.0),
                                 line=goal_line(p, words)[2:]))
            else:
                sec.append(goal_line(p, words))
        if f.activity_hours >= 0.1:
            sec.append(words("reports.activity", hours=f.activity_hours))
        sec += [words("reports.not_pushed", title=t) for t in digest.get(plan.id, ())]
        if sec:
            lines += ["", words("reports.section", title=plan.title)] + sec
            sections.append({"domain": plan.id, "title": plan.title, "lines": sec})
    top = top_priorities(tomorrow)
    if top:
        lines += ["", words("reports.tomorrow_first")] + _priority_lines(top, words)
    return InboxItem(
        id=None,
        ts=now,
        domain=REPORT_DOMAIN,
        kind="summary",
        title=words("reports.evening_title", day=day),
        body="\n".join(lines).strip(),
        data={"sections": sections, "tomorrow": [_priority_data(p, a.plan.id) for a, p in top]},
    )


def weekly_report(agendas: Sequence[DomainAgenda], week_start: date, now: datetime, *, words: Language) -> InboxItem:
    """Includes pivot, decision_tree and filters: rendered locally, never sent to an LLM."""
    lines: list[str] = []
    for a in sorted(agendas, key=lambda a: a.plan.priority):
        plan = a.plan
        sec = [goal_line(p, words) for p in a.goals]
        sec += [
            words("reports.deadline", title=s.deadline.title, due=s.deadline.due, days=s.days_left)
            for s in a.deadlines
            if s.days_left <= 30
        ]
        if plan.filters:
            sec += [words("reports.filters")] + [f"• {f}" for f in plan.filters]
        if plan.pivot:
            p = plan.pivot
            sec += [words("reports.pivot", date=p.get("date"), cond=p.get("if"), then=p.get("then"))]
        if plan.decision_tree:
            sec += [words("reports.decision_tree")] + [f"• {n.get('when')} → {n.get('then')}" for n in plan.decision_tree]
        if sec:
            lines += ["", words("reports.section", title=plan.title)] + sec
    return InboxItem(
        id=None,
        ts=now,
        domain=REPORT_DOMAIN,
        kind="weekly",
        title=words("reports.weekly_title", week_start=week_start),
        body="\n".join(lines).strip(),
        data={"domains": [agenda_data(a, words) for a in agendas]},
    )
