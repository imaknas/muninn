"""The invented suite shipped with Muninn: its cases, how they are scored, and a model that answers them right."""

from __future__ import annotations

import json
from pathlib import Path

from muninn.adapters.llm.reply_check import ReplyCheck, schemas
from muninn.core import eval_suite
from muninn.core.eval_suite import SuiteCase, in_wrong_language, score
from muninn.main import load_suite, suite_systems
from muninn.package import CONTRACTS

CONTRACTS = CONTRACTS


SAID_RIGHT = {"zh-TW": "好", "en": "Done"}  # a route reply in each language


def right(case: SuiteCase) -> str:
    """The reply the case expects, as a model would write it."""
    if case.task == "route":
        return json.dumps({"actions": [{"tool": e["tool"], "args": e.get("args") or {}} for e in case.expect],
                           "reply": SAID_RIGHT[case.language]}, ensure_ascii=False)
    if case.task == "facts":
        return json.dumps({"domain": None, "effects": [{**e, "why": "因為"} for e in case.expect], "projects": []}, ensure_ascii=False)
    return json.dumps({"domain": case.expect})


def every_case() -> list[SuiteCase]:
    return [c for code, suite in load_suite().items() for c in eval_suite.cases(suite, code)]


def test_the_suite_has_cases_for_each_task_and_its_answers_keep_to_the_declared_shapes():
    suite = load_suite()
    assert set(suite["zh-TW"]) == set(eval_suite.TASKS)  # a language may have no file yet; zh-TW has every task
    cases = every_case()
    for code in suite:
        by = {t: [c for c in cases if c.task == t and c.language == code] for t in suite[code]}
        assert all(len(v) >= 10 for v in by.values())
    check = ReplyCheck(schemas(CONTRACTS))
    assert [c.said for c in cases if check(c.task, right(c))] == []  # every expected answer is one a model may give


def test_a_model_answering_every_case_right_scores_full():
    cases = every_case()
    systems = {(c.task, c.language): f"{c.task} in {c.language}" for c in cases}
    answers = {(systems[(c.task, c.language)], c.prompt): right(c) for c in cases}
    reports = eval_suite.run(cases, systems, {t: "fake:model" for t in eval_suite.TASKS},
                             lambda task, system, prompt, json_mode: answers[(system, prompt)], ReplyCheck(schemas(CONTRACTS)))
    assert set(reports) == {(c.task, c.language) for c in cases}
    assert {k: (r.mean, r.off_shape, r.wrong_language, r.misses) for k, r in reports.items()} == {k: (1.0, 0, 0, []) for k in reports}


def test_each_case_is_asked_with_the_prompt_for_its_language():
    systems = suite_systems(["zh-TW", "en"], "- today: x")
    assert systems[("route", "zh-TW")] != systems[("route", "en")] and "English" in systems[("route", "en")]
    assert "Traditional Chinese" in systems[("facts", "zh-TW")] and "English" in systems[("facts", "en")]
    cases = [SuiteCase("work_check", "a", "{}", "work", (), "zh-TW"), SuiteCase("work_check", "b", '{"b": 1}', "work", (), "en")]
    seen = []
    eval_suite.run(cases, {("work_check", "zh-TW"): "zh", ("work_check", "en"): "en"}, {"work_check": "m"},
                   lambda task, system, prompt, json_mode: seen.append((system, prompt)) or '{"domain": "work"}', lambda t, r: [])
    assert sorted(seen) == [("en", '{"b": 1}'), ("zh", "{}")]


def test_a_route_reply_not_in_the_cases_language_is_counted_and_scored_as_it_is():
    zh = SuiteCase("route", "今天先做什麼?", "{}", [{"tool": "today"}], (), "zh-TW")
    en = SuiteCase("route", "What first today?", "{}", [{"tool": "today"}], (), "en")
    reply = lambda said: json.dumps({"actions": [{"tool": "today", "args": {}}], "reply": said}, ensure_ascii=False)  # noqa: E731
    assert in_wrong_language(zh, reply("Here is today")) and not in_wrong_language(zh, reply("今天先寫作品集"))
    assert in_wrong_language(en, reply("今天先寫作品集")) and not in_wrong_language(en, reply("Here is today"))
    assert not in_wrong_language(en, reply("")) and not in_wrong_language(en, "not json")  # nothing said: nothing to count
    reports = eval_suite.run([zh, en], {("route", "zh-TW"): "s", ("route", "en"): "s"}, {"route": "m"},
                             lambda task, system, prompt, json_mode: reply("Here is today"), lambda t, r: [])
    assert (reports[("route", "zh-TW")].wrong_language, reports[("route", "en")].wrong_language) == (1, 0)
    assert reports[("route", "zh-TW")].mean == reports[("route", "en")].mean == 1.0  # the score is the tools'


def test_route_is_scored_on_the_tools_and_the_ids_a_case_names_and_nothing_made_up():
    case = SuiteCase("route", "x", "{}", [{"tool": "set_status", "args": {"target": "jobs/acme", "status": "applied"}},
                                         {"tool": "propose_domain"}])
    reply = lambda *actions: json.dumps({"actions": list(actions), "reply": ""})  # noqa: E731
    acme = {"tool": "set_status", "args": {"target": "jobs/acme", "status": "applied"}}
    assert score(case, reply(acme, {"tool": "propose_domain", "args": {"id": "pottery", "title": "陶藝"}})) == 1.0
    assert score(case, reply({"tool": "set_status", "args": {"target": "jobs/acme", "status": "interview"}})) == 0.0
    assert 0 < score(case, reply(acme)) < 1
    assert score(SuiteCase("route", "x", "{}", []), reply()) == 1.0  # nothing to do, nothing done
    assert score(case, "我不知道") == 0.0


def test_facts_and_the_work_check_are_scored_on_what_they_decide():
    facts = SuiteCase("facts", "x", "{}", [{"id": "todo:a:0", "effect": "resolves"}])
    assert score(facts, json.dumps({"effects": [{"id": "todo:a:0", "effect": "resolves"}]})) == 1.0
    assert score(facts, json.dumps({"effects": [{"id": "todo:a:0", "effect": "changes"}]})) == 0.0
    work = SuiteCase("work_check", "x", "{}", "work")
    assert score(work, '{"domain": "work"}') == 1.0 and score(work, '{"domain": "jobs"}') == 0.0 and score(work, "嗯") == 0.0


def test_a_tool_a_case_allows_or_an_item_only_noted_is_not_held_against_a_model():
    case = SuiteCase("route", "x", "{}", [{"tool": "set_status", "args": {"target": "jobs/b", "status": "interview"}}], ("record_fact",))
    acme = {"tool": "set_status", "args": {"target": "jobs/b", "status": "interview"}}
    assert score(case, json.dumps({"actions": [acme, {"tool": "record_fact", "args": {"fact": "面試"}}]})) == 1.0
    assert score(case, json.dumps({"actions": [acme, {"tool": "today", "args": {}}]})) < 1.0
    facts = SuiteCase("facts", "x", "{}", [{"id": "todo:a:0", "effect": "resolves"}])
    noted = {"id": "todo:b:1", "effect": "relevant"}
    assert score(facts, json.dumps({"effects": [{"id": "todo:a:0", "effect": "resolves"}, noted]})) == 1.0
    assert score(facts, json.dumps({"effects": [{"id": "todo:a:0", "effect": "resolves"}, {**noted, "effect": "changes"}]})) < 1.0
