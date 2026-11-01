"""Checking a change to the prompts against what really happened: each recorded case is run with the prompt as it was
and as it is now, on the model its task uses, and the decisions are compared. A case that came out differently is
run again both ways, so a model that answers differently on its own is told apart from the change itself.

The prompts are given as two snapshots ({task: system prompt}); a task's prompt may hold the place a case filled in
(the lent project's name, `%s`), read back from the system the case was recorded with."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from .evals import SCORERS, request_text
from .reply_json import json_object

Complete = Callable[[str, str, str, bool], "str | None"]  # (task, system, prompt, json_mode) → the reply


# what a reply decided, for the tasks whose replies also carry words of their own (a reason, a sentence to pass on):
# only the decision is compared, since the same decision is worded differently each time
DECIDED: dict[str, Callable[[dict[str, Any]], Any]] = {
    "lent_turn": lambda d: (d.get("topic"), bool(d.get("for_muninn")), d.get("end") is True),
    "turn_topic": lambda d: d.get("topic"),
    "task_evidence": lambda d: sorted(str(x.get("task")) for x in d.get("done") or [] if isinstance(x, dict)),
    "task_match": lambda d: sorted((str(x.get("new")), str(x.get("as"))) for x in d.get("same") or [] if isinstance(x, dict)),
    "idea_next": lambda d: d.get("next"),
}


def decision(task: str, raw: str | None) -> Any:
    """What a reply decided, as compared: its scorer's keys where the task has one, else what DECIDED reads from its
    JSON, else its JSON without the reasons it gives, else its text."""
    scorer = SCORERS.get(task)
    if scorer is not None:
        got = scorer.keys(raw)
        return sorted(got, key=repr) if isinstance(got, set) else got
    if raw is None:
        return None
    data = json_object(raw)
    if data is not None:
        if task in DECIDED:
            return DECIDED[task](data)
        return {k: v for k, v in sorted(data.items()) if k not in ("why", "summary")}
    return raw.strip()


def system_for(snapshot: dict[str, str], case: dict[str, Any]) -> str | None:
    """The system prompt of the case's task in this snapshot, filled in as the case's own was; None when the snapshot
    has none for that task."""
    text = snapshot.get(case["task"])
    if text is None:
        return None
    if "%s" in text:
        m = re.search(r'lent to the project "([^"]*)"', case.get("system") or "")
        text = text % (m.group(1) if m else "")
    return text


@dataclass
class CaseResult:
    case: int
    task: str
    said: str
    before: Any
    after: Any
    again: list[tuple[Any, Any]] = field(default_factory=list)  # (before, after) of the second run, when it changed

    @property
    def changed(self) -> bool:
        return self.before != self.after

    @property
    def verdict(self) -> str:
        """same; changed (the change did it: both runs agree with themselves); noisy (the model answers this one
        differently on its own)."""
        if not self.changed:
            return "same"
        steady = all(b == self.before and a == self.after for b, a in self.again)
        return "changed" if steady else "noisy"


def run(cases: Sequence[dict[str, Any]], before: dict[str, str], after: dict[str, str], complete: Complete,
        workers: int = 4, on_case: Callable[[int, int], None] | None = None) -> list[CaseResult]:
    """Every case whose task's prompt differs between the snapshots, compared; the ones that changed run twice.
    The same snapshot twice is the baseline: every case of its tasks, run twice as it is (what changes then is the
    model answering differently on its own)."""
    baseline = before == after
    todo = [c for c in cases if system_for(before, c) is not None and (baseline or system_for(before, c) != system_for(after, c))]
    done = [0]

    def one(case: dict[str, Any]) -> CaseResult:
        prompt = request_text(case)
        ask = lambda snap: decision(case["task"], complete(case["task"], system_for(snap, case) or "", prompt,  # noqa: E731
                                                           bool(case.get("json_mode"))))
        r = CaseResult(case["id"], case["task"], _said(case), ask(before), ask(after))
        if r.changed and not baseline:
            r.again.append((ask(before), ask(after)))
        done[0] += 1
        if on_case:
            on_case(done[0], len(todo))
        return r

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(one, todo))


def _said(case: dict[str, Any]) -> str:
    d = case.get("data") or {}
    for k in ("said", "text", "sentence", "fact"):
        if isinstance(d.get(k), str):
            return d[k][:160]
    return request_text(case)[:160]


def summary(results: Sequence[CaseResult]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in results:
        row = out.setdefault(r.task, {"cases": 0, "same": 0, "changed": 0, "noisy": 0})
        row["cases"] += 1
        row[r.verdict] += 1
    return out
