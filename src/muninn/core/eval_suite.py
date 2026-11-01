"""A small suite of invented cases (src/muninn/eval_suite/<task>.<language>.yaml) for the decision points a new model
most has to get right: what a sentence asks Muninn to do (route), what news settles (facts), and whether a sentence
is about work (work_check). Anyone can run it on any model they set up, with none of their own data: it says how a
model does before it answers for real, and whether its replies keep to the declared shapes.

Each language Muninn speaks has its own cases, said the way a user of that language says them, and each case is
asked with the prompt as Muninn builds it for its language; a task is reported per language. A route reply whose
sentence to the user is not in the case's language is counted as such (it does not change the score).

Each case gives the request as the point would send it and the answer it should get. Route is scored on the tools
chosen and on the id-like arguments a case names (a new name a model makes up is not held against it), a tool the
case lists under `also` being one a model may add; facts on which items are resolved or changed and how (an item
only noted as relevant is not held against it); work_check on the one domain."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .commands import read_route as parse_route
from .evals import ID_ARGS, _f1, _fact_pairs
from .reply_json import json_object

TASKS = ("route", "facts", "work_check")
Ask = Callable[[str, str, str, bool], "str | None"]  # (task, system, prompt, json_mode) → the reply
HAN = re.compile(r"[\u4e00-\u9fff]")
# whether a text is written in the language, by code: what a route reply to a case of that language should be in
WRITTEN_IN: dict[str, Callable[[str], bool]] = {"zh-TW": lambda text: bool(HAN.search(text)), "en": lambda text: not HAN.search(text)}


@dataclass(frozen=True)
class SuiteCase:
    task: str
    said: str
    prompt: str
    expect: Any
    also: tuple[str, ...] = ()  # route: tools a model may add without it counting against it
    language: str = ""  # the language the case is said in (its file's), and asked in; "" for none in particular


@dataclass
class SuiteReport:
    task: str
    model: str
    language: str = ""
    scores: list[float] = field(default_factory=list)
    off_shape: int = 0  # replies that did not keep to the point's declared shape
    wrong_language: int = 0  # route: replies whose sentence to the user is not in the case's language
    ms: list[int] = field(default_factory=list)
    misses: list[dict[str, Any]] = field(default_factory=list)

    @property
    def mean(self) -> float | None:
        return sum(self.scores) / len(self.scores) if self.scores else None


def cases(suite: Mapping[str, Mapping[str, Any]], language: str) -> list[SuiteCase]:
    """The cases of each task in one language's suite, with their requests as the decision point builds them."""
    out = []
    for task, s in suite.items():
        ctx = dict(s.get("context") or {})
        for c in s.get("cases") or []:
            if task == "route":
                said, data = c["said"], {"context": ctx, "said": c["said"]}
            elif task == "facts":
                said, data = c["fact"], {**ctx, "fact": c["fact"]}
            elif task == "work_check":
                said, data = c["text"], {**ctx, "text": c["text"]}
            else:
                raise ValueError(f"eval suite: no task {task!r}; known: {', '.join(TASKS)}")
            out.append(SuiteCase(task, said, json.dumps(data, ensure_ascii=False), c.get("expect"), tuple(c.get("also") or ()), language))
    return out


def score(case: SuiteCase, reply: str | None) -> float:
    if case.task == "route":
        named: dict[str, set[str]] = {}
        for e in case.expect or []:
            named.setdefault(e["tool"], set()).update(k for k in (e.get("args") or {}) if k in ID_ARGS)
        ref = {(e["tool"], tuple(sorted((k, str(v)) for k, v in (e.get("args") or {}).items() if k in ID_ARGS)))
               for e in case.expect or []}
        plan = parse_route(reply)
        if plan is None:
            return 0.0
        got = {(a["tool"], tuple(sorted((k, str(v)) for k, v in (a.get("args") or {}).items() if k in named.get(a["tool"], ()))))
               for a in plan["actions"] if a["tool"] not in case.also or a["tool"] in named}
        return _f1(ref, got)
    if case.task == "facts":
        got = _fact_pairs(reply)
        ref = {(e["id"], e["effect"]) for e in case.expect or []}
        return 0.0 if got is None else _f1(ref, {g for g in got if g[1] != "relevant" or g in ref})
    d = json_object(reply)
    if d is None:
        return 0.0
    domain = d.get("domain")
    return 1.0 if str(domain) == str(case.expect) else 0.0


def in_wrong_language(case: SuiteCase, reply: str | None) -> bool:
    """Whether a route reply's sentence to the user is not in the case's language (one with no sentence is not)."""
    plan = parse_route(reply) if case.task == "route" else None
    said = (plan or {}).get("reply") or ""
    written = WRITTEN_IN.get(case.language)
    return bool(said.strip()) and written is not None and not written(said)


def run(suite_cases: list[SuiteCase], systems: Mapping[tuple[str, str], str], models: Mapping[str, str], ask: Ask,
        check: Callable[[str, str], list[str]], workers: int = 4, timer: Callable[[], float] = time.monotonic,
        on_case: Callable[[int, int], None] | None = None) -> dict[tuple[str, str], SuiteReport]:
    """Each task's report in each language, by (task, language): `systems` the prompt for each, `models` the name of
    the model asked for each task, `check` what is off the point's declared shape."""
    reports = {(c.task, c.language): SuiteReport(c.task, models[c.task], c.language) for c in suite_cases}
    done = [0]

    def one(case: SuiteCase) -> tuple[SuiteCase, str | None, int]:
        started = timer()
        try:
            reply = ask(case.task, systems[(case.task, case.language)], case.prompt, True)
        except Exception as e:  # noqa: BLE001 — a failing model scores zero on that case
            reply = f"<error: {e}>"
        done[0] += 1
        if on_case:
            on_case(done[0], len(suite_cases))
        return case, reply, round((timer() - started) * 1000)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for case, reply, ms in pool.map(one, suite_cases):
            rep = reports[(case.task, case.language)]
            s = score(case, reply)
            rep.scores.append(s)
            rep.ms.append(ms)
            if reply is None or check(case.task, reply):
                rep.off_shape += 1
            if in_wrong_language(case, reply):
                rep.wrong_language += 1
            if s < 1.0:
                rep.misses.append({"said": case.said, "expected": case.expect, "reply": (reply or "")[:400], "score": round(s, 2)})
    return reports
