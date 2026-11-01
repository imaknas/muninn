"""Decides which signals become inbox items and which of them interrupt.

- The same signal key reaches the inbox once per local day, again only if its
  level went up (it got worse).
- Every new item is stored. Whether it is also pushed depends on its domain:
  domains up to `interrupt_max_priority` push; lower ones stay in the inbox
  and the evening summary unless the signal escalates to `escalation_level`;
  observe-only domains (no goals) never push.
- Each priority tier has its own daily push quota, so a noisy low-priority
  domain cannot use up the pushes a high-priority one needs. There is also an
  overall daily cap.
- Quiet hours hold pushes back, except for signals marked bypass_quiet.
- Scheduled reports (brief/summary/weekly) skip quotas but respect quiet hours.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Mapping, Protocol, Sequence

from .language import Language
from .models import InboxItem, Signal
from .ports import InboxStore, PushLog, PushMessage, SignalStore
from .timewin import in_window, local

ESCALATION = "escalation"


@dataclass(frozen=True)
class DomainPolicy:
    priority: int = 3
    observe_only: bool = False


@dataclass(frozen=True)
class ComposerConfig:
    tz: str = "UTC"
    quiet_start: time = time(23, 0)
    quiet_end: time = time(8, 0)
    daily_push_cap: int = 8
    interrupt_max_priority: int = 3
    escalation_level: int = 3
    tier_quota: Mapping[str, int] = field(default_factory=lambda: {"1": 4, "2": 2, "3": 1, ESCALATION: 1})
    push_titles: bool = True  # False: notifications say only "<domain>: N new reminders"


@dataclass(frozen=True)
class Composition:
    items: tuple[InboxItem, ...]
    pushes: tuple[PushMessage, ...]
    tiers: Mapping[str, str] = field(default_factory=dict)  # domain → quota tier used


def render_signal_body(s: Signal) -> str:
    lines = [s.detail] if s.detail else []
    lines += [f"• {a}" for a in s.actions]
    return "\n".join(lines)


class ComposerStorage(InboxStore, SignalStore, PushLog, Protocol):
    """Where signals land, how far each was already raised, and the pushes counted against the quota."""


class Composer:
    def __init__(self, store: ComposerStorage, config: ComposerConfig, *, words: Language):
        self._store = store
        self._cfg = config
        self.words = words

    @property
    def config(self) -> ComposerConfig:
        return self._cfg

    def quiet(self, now: datetime) -> bool:
        return in_window(local(now, self._cfg.tz).time(), self._cfg.quiet_start, self._cfg.quiet_end)

    def tier(self, s: Signal, policy: DomainPolicy) -> str | None:
        """The quota tier a signal may push under, or None for inbox only."""
        if policy.observe_only:
            return None
        if policy.priority <= self._cfg.interrupt_max_priority:
            return str(policy.priority)
        if s.level >= self._cfg.escalation_level:
            return ESCALATION
        return None

    def compose_alerts(
        self, signals: Sequence[Signal], now: datetime, policies: Mapping[str, DomainPolicy] = {}
    ) -> Composition:
        day = local(now, self._cfg.tz).date()
        quiet = self.quiet(now)
        items: list[InboxItem] = []
        pushable: dict[str, list[InboxItem]] = defaultdict(list)
        tiers: dict[str, str] = {}
        for s in sorted(signals, key=lambda s: (s.domain_priority, s.priority, -s.level)):
            prev = self._store.signal_level(s.key, day)
            if prev is not None and s.level <= prev:
                continue
            self._store.record_signal(s.key, day, s.level)
            policy = policies.get(s.domain, DomainPolicy(priority=s.domain_priority))
            tier = self.tier(s, policy)
            push = tier is not None and (not quiet or s.bypass_quiet)
            item = self._store.add_inbox(
                InboxItem(
                    id=None,
                    ts=now,
                    domain=s.domain,
                    kind="alert",
                    title=s.title,
                    body=render_signal_body(s),
                    data={
                        "actions": list(s.actions),
                        "kind": s.kind,
                        "sensitivity": s.sensitivity.label,
                        "digest_only": tier is None,
                        "quiet_hold": tier is not None and not push,
                        "domain_priority": s.domain_priority,
                    },
                    signal_key=s.key,
                    level=s.level,
                )
            )
            items.append(item)
            if push:
                pushable[s.domain].append(item)
                tiers.setdefault(s.domain, tier)

        used = dict(self._store.pushes_by_tier(day))
        total = sum(used.values())
        pushes = []
        for domain in pushable:  # already in priority order
            tier = tiers[domain]
            if total >= self._cfg.daily_push_cap or used.get(tier, 0) >= self._cfg.tier_quota.get(tier, 0):
                continue
            first = pushable[domain][0]  # signals were sorted by priority
            pushes.append(PushMessage(
                domain, len(pushable[domain]),
                title=first.title if self._cfg.push_titles else None,
                sensitive=first.data.get("sensitivity") == "sensitive",
                more=self.words("composer.more", n=len(pushable[domain]) - 1),
            ))
            used[tier] = used.get(tier, 0) + 1
            total += 1
        return Composition(tuple(items), tuple(pushes), {m.domain: tiers[m.domain] for m in pushes})

    def compose_report(self, item: InboxItem, now: datetime) -> Composition:
        stored = self._store.add_inbox(item)
        title = (stored.data.get("push_title") or stored.title) if self._cfg.push_titles else None
        pushes = () if self.quiet(now) else (PushMessage(stored.domain, 1, title=title),)
        return Composition((stored,), pushes)
