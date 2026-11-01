"""Turn plain plan/tracker data (already parsed from YAML) into DomainPlan."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from .models import (
    ALL_STATUSES,
    Deadline,
    DomainPlan,
    DoneWhen,
    Goal,
    Sensitivity,
    Target,
    TrackerItem,
)


class PlanError(ValueError):
    pass


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _target_title(raw: dict[str, Any]) -> str:
    company, role = raw.get("company"), raw.get("role")
    if company and role:
        return f"{company} {role}"
    return str(raw.get("title") or company or role or raw["id"])


GOAL_KEYS = {"id", "title", "metric", "target", "period", "priority", "if_behind", "private"}


def parse_goal(raw: dict[str, Any]) -> Goal:
    period = raw.get("period", "week")
    if period not in ("day", "week"):
        raise PlanError(f"goal {raw.get('id')}: period must be day|week, got {period!r}")
    return Goal(
        id=str(raw["id"]),
        title=str(raw.get("title", raw["id"])),
        metric=str(raw["metric"]),
        target=float(raw["target"]),
        period=period,
        priority=int(raw.get("priority", 3)),
        if_behind=tuple(str(a) for a in raw.get("if_behind") or ()),
        private=bool(raw.get("private", False)),
        params={k: v for k, v in raw.items() if k not in GOAL_KEYS},
    )


def parse_done_when(raw: Any) -> DoneWhen:
    if raw in (None, "manual"):
        return DoneWhen(manual=True)
    if isinstance(raw, dict) and "targets" in raw:
        threshold = str(raw.get("status_at_least", "applied"))
        if threshold not in ALL_STATUSES:
            raise PlanError(f"unknown status_at_least {threshold!r}")
        return DoneWhen(
            manual=False,
            targets=tuple(str(t) for t in raw["targets"]),
            status_at_least=threshold,
        )
    raise PlanError(f"done_when must be 'manual' or {{targets, status_at_least}}, got {raw!r}")


def parse_deadline(raw: dict[str, Any]) -> Deadline:
    due = _date(raw.get("due"))
    if due is None:
        raise PlanError(f"deadline {raw.get('id')}: missing due")
    return Deadline(
        id=str(raw["id"]),
        title=str(raw.get("title", raw["id"])),
        due=due,
        lead_days=int(raw.get("lead_days", 1)),
        priority=int(raw.get("priority", 3)),
        done_when=parse_done_when(raw.get("done_when")),
        goal=raw.get("goal"),
        private=bool(raw.get("private", False)),
        source=str(raw["source"]) if raw.get("source") else None,
    )


def parse_target(raw: dict[str, Any]) -> Target:
    return Target(
        id=str(raw["id"]),
        title=_target_title(raw),
        priority=int(raw.get("priority", 3)),
        url=raw.get("url"),
        notes=raw.get("notes"),
    )


def parse_tracker_item(raw: dict[str, Any]) -> TrackerItem:
    status = str(raw.get("status", "todo"))
    if status not in ALL_STATUSES:
        raise PlanError(f"tracker {raw.get('target')}: unknown status {status!r}")
    title = _target_title({**raw, "id": raw["target"]}) if raw.get("company") else None
    return TrackerItem(
        target=str(raw["target"]),
        status=status,
        title=title,
        applied=_date(raw.get("applied")),
        next_action=raw.get("next_action"),
        next_action_due=_date(raw.get("next_action_due")),
    )


def parse_plan(
    raw: dict[str, Any],
    tracker_rows: Iterable[dict[str, Any]] = (),
    default_timezone: str = "UTC",
) -> DomainPlan:
    dom = raw.get("domain") or {}
    if "id" not in dom:
        raise PlanError("plan is missing domain.id")
    return DomainPlan(
        id=str(dom["id"]),
        title=str(dom.get("title", dom["id"])),
        timezone=str(raw.get("timezone") or default_timezone),
        sensitivity_default=Sensitivity.parse(dom.get("sensitivity_default"), Sensitivity.PERSONAL),
        priority=int(dom.get("priority", 3)),
        goals=tuple(parse_goal(g) for g in raw.get("goals") or ()),
        deadlines=tuple(parse_deadline(d) for d in raw.get("deadlines") or ()),
        targets=tuple(parse_target(t) for t in raw.get("targets") or ()),
        tracker=tuple(parse_tracker_item(r) for r in tracker_rows),
        idle_alert_days=dom.get("idle_alert_days", raw.get("idle_alert_days")),
        pivot=raw.get("pivot"),
        decision_tree=tuple(raw.get("decision_tree") or ()),
        filters=tuple(str(f) for f in raw.get("filters") or ()),
    )
