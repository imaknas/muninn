"""A limit on what the models may cost in a day (config budget.daily_usd).

What a day has cost is counted from the tokens each provider said its replies took (kept for every reply, however
the model was reached: `Metered`) at the prices config gives each model. Once the day's cost reaches the limit, no model is asked
until the next local day: every decision point then does what it does when a model does not answer (its on_failure
in contracts/llm-points.yaml), and the user is told once that day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from .ports import Clock, LLMBackend, Reply


@dataclass(frozen=True)
class Price:
    """What a model costs, in US dollars per million tokens."""
    input: float
    output: float


class Metered:
    """A model backend whose every reply's tokens are kept: a decision point's, a judge's, an evaluation's alike."""

    def __init__(self, inner: LLMBackend, keep: Callable[[str, int, int], None]):
        """`keep(backend name, tokens in, tokens out)`: a reply whose provider said what it took."""
        self._inner, self._keep = inner, keep

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def complete(self, system: str, prompt: str, **kw: Any) -> str:
        reply = self._inner.complete(system, prompt, **kw)
        if isinstance(reply, Reply) and (reply.input_tokens or reply.output_tokens):
            self._keep(self._inner.name, reply.input_tokens or 0, reply.output_tokens or 0)
        return reply


class DailyBudget:
    def __init__(self, limit_usd: float, prices: Mapping[str, Price], used_since: Callable[[datetime], Iterable[tuple[str | None, int, int]]],
                 clock: Clock, get_state: Callable[[str], str | None], set_state: Callable[[str, str], None],
                 on_over: Callable[[float, float], None]):
        """`prices`: by backend name (provider:model). `used_since(t)`: (backend, input tokens, output tokens) of
        every reply since t. `clock`: the user's time, whose days the limit is for. `on_over(spent, limit)`: tell the
        user, called once a day when the limit is reached."""
        self.limit = limit_usd
        self._prices, self._used_since, self._clock = dict(prices), used_since, clock
        self._get, self._set, self._on_over = get_state, set_state, on_over

    def spent(self) -> float:
        """What today has cost so far (a model without a price counts nothing: config check says so)."""
        start = self._clock.now().replace(hour=0, minute=0, second=0, microsecond=0)
        total = 0.0
        for backend, tokens_in, tokens_out in self._used_since(start):
            p = self._prices.get(backend or "")
            if p is not None:
                total += (tokens_in * p.input + tokens_out * p.output) / 1_000_000
        return total

    def over(self) -> bool:
        """Whether today's limit is reached (the user told the first time it is, that day)."""
        spent = self.spent()
        if spent < self.limit:
            return False
        today = self._clock.now().date().isoformat()
        if self._get("budget:said") != today:
            self._set("budget:said", today)
            self._on_over(spent, self.limit)
        return True
