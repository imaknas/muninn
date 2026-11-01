from __future__ import annotations

import json

from muninn.core import prompt_check


def case(i, task, said, system="old"):
    return {"id": i, "task": task, "system": system, "data": {"said": said}, "json_mode": True}


def test_only_cases_whose_prompt_changed_run_and_a_change_is_told_from_a_wobble():
    before = {"mail_triage": "sort mail", "lent_turn": 'lent to the project "%s"', "facts": "same"}
    after = {"mail_triage": "sort mail, plainly", "lent_turn": 'lent to the project "%s" now', "facts": "same"}
    flips = {"wobble": iter(["fyi", "promo", "promo", "fyi"])}  # the same mail read four times, two ways

    def complete(task, system, prompt, json_mode):
        said = json.loads(prompt)["said"]
        if task == "lent_turn":
            assert 'project "sofa-fit"' in system  # filled in as the case's own was
            return json.dumps({"topic": "current", "end": "now" in system, "why": "x"})
        if said == "wobble":
            return json.dumps({"category": next(flips["wobble"])})
        return json.dumps({"category": "promo" if "plainly" in system and said == "ad" else "fyi"})

    cases = [case(1, "mail_triage", "ad"), case(2, "mail_triage", "bill"), case(3, "mail_triage", "wobble"),
             case(4, "lent_turn", "今天先這樣", 'lent to the project "sofa-fit"'), case(5, "facts", "x")]
    got = {r.case: r for r in prompt_check.run(cases, before, after, complete, workers=2)}
    assert set(got) == {1, 2, 3, 4}  # the facts prompt did not change: not run
    assert got[1].verdict == "changed" and (got[1].before, got[1].after) == ("fyi", "promo")
    assert got[2].verdict == "same"
    assert got[3].verdict == "noisy"  # changes on its own between runs
    assert got[4].after == ("current", False, True)  # the decision only: where it goes, anything for Muninn, ended
    assert prompt_check.summary(got.values())["mail_triage"] == {"cases": 3, "same": 1, "changed": 1, "noisy": 1}
    flips["wobble"] = iter(["fyi", "promo"])
    base = {r.case: r for r in prompt_check.run(cases, before, before, complete)}
    assert set(base) == {1, 2, 3, 4, 5} and base[3].changed and not base[1].changed  # the baseline: every case, run twice


def test_the_users_word_on_two_todos_is_the_reference():
    from muninn.core.evals import PairsScorer

    case = {"feedback": [{"kind": "same_task", "todo": "shoes:1", "as": "task:shoes", "same": True},
                         {"kind": "same_task", "todo": "paper:2", "as": "deadline:paper", "same": False}]}
    ref = PairsScorer().adjust(case, {("paper:2", "deadline:paper")})
    assert ref == {("shoes:1", "task:shoes")}
