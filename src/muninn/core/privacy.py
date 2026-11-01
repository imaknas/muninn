"""Builds the only payloads allowed to leave the machine.

Whitelist, not blacklist: titles, next_action text and progress numbers. Never
pivot, decision_tree, target notes, URLs, observations or free-text notes.
Plans whose default sensitivity is `sensitive`, and goals/deadlines marked
`private: true`, are left out entirely.

One exception the user chose: their own words, once a WorkCheck has cleared
them as not about work, may go to the cloud with the context needed to
understand them (understanding_payload). That context is built without the
work domains by the caller (Engine.understanding_plans).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Sequence

from .llm_router import CloudSafe, _make_cloud_safe
from .models import DomainPlan, MailMessage, Posting, Sensitivity
from .progress import DeadlineStatus, GoalProgress


@dataclass(frozen=True)
class DayFacts:
    plan: DomainPlan
    day: date
    goals: Sequence[GoalProgress]
    today_by_goal: dict[str, float]  # goal id → amount done today
    applied_today: Sequence[str]  # target ids
    deadlines: Sequence[DeadlineStatus]  # open ones
    alerts_today: int
    activity_hours: float = 0.0  # all observed activity attributed to the domain today


def _num(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


def evening_payload(f: DayFacts) -> CloudSafe | None:
    plan = f.plan
    if plan.sensitivity_default >= Sensitivity.SENSITIVE:
        return None
    tomorrow = f.day + timedelta(days=1)
    data = {
        "domain": plan.id,
        "date": f.day.isoformat(),
        "goals": [
            {
                "title": p.goal.title,
                "period": p.goal.period,
                "done": _num(p.actual),
                "target": _num(p.target),
                "expected_by_now": _num(p.expected),
                "today": _num(f.today_by_goal.get(p.goal.id, 0.0)),
                "unit": p.unit,
                "direction": p.direction,
            }
            for p in f.goals
            if not p.goal.private
        ],
        "applied_today": [plan.target_title(t) for t in f.applied_today],
        "deadlines": [
            {"title": s.deadline.title, "days_left": s.days_left, "pending": len(s.pending_targets)}
            for s in f.deadlines
            if not s.deadline.private and s.days_left <= max(s.deadline.lead_days, 7)
        ],
        "next_actions_due_by_tomorrow": [
            {"title": plan.target_title(i.target), "next_action": i.next_action}
            for i in plan.tracker
            if not i.is_closed and i.next_action and i.next_action_due and i.next_action_due <= tomorrow
        ],
        "alerts_today": f.alerts_today,
        "activity_hours_today": _num(f.activity_hours),
    }
    return _make_cloud_safe(json.dumps(data, ensure_ascii=False))


def packet_payload(
    plan: DomainPlan,
    target_title: str,
    next_action: str | None,
    posting: Posting,
    documents: Sequence[tuple[str, str]],
    answer_language: str = "English",
) -> CloudSafe | None:
    """Draft material for one application, for a cloud model.

    Holds only the public posting, the user's own reference documents (which
    the user chose to allow for drafting) and the tracker's next_action. Never
    the plan's notes, pivot, decision_tree, filters, or any observation.
    Sensitive domains get None (draft locally or not at all)."""
    if plan.sensitivity_default >= Sensitivity.SENSITIVE:
        return None
    data = {
        "target": target_title,
        "next_action": next_action,
        "answer_language": answer_language,
        "posting": {"url": posting.url, "title": posting.title, "location": posting.location, "text": posting.text[:20_000]},
        "documents": [{"name": n, "text": t[:15_000]} for n, t in documents],
    }
    return _make_cloud_safe(json.dumps(data, ensure_ascii=False))


def mail_payload(m: MailMessage) -> CloudSafe | None:
    """One personal email for a cloud model, as the user allowed for personal
    mail. Work mail never gets a cloud payload."""
    if m.account_kind != "personal":
        return None
    data = {"from": m.sender, "subject": m.subject, "date": m.ts.isoformat(), "body": m.text}
    return _make_cloud_safe(json.dumps(data, ensure_ascii=False))


UNDERSTANDING_DOMAIN = "personal"  # the request domain of cleared words: the user's life outside work


@dataclass(frozen=True)
class Cleared:
    """Words a WorkCheck found not to be about work."""

    text: str


def understanding_payload(data: dict[str, Any], cleared: Cleared) -> CloudSafe | None:
    if not isinstance(cleared, Cleared):
        return None
    return _make_cloud_safe(json.dumps(data, ensure_ascii=False, default=str))
