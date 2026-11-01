

def test_an_answer_that_is_not_an_object_is_no_answer():
    from muninn.core.todo_match import _parse

    assert _parse('{"same": [{"todo": "a", "as": "m1"}]}', {"m1"}, ["a"]) == {"a": "m1"}
    assert _parse("[1, 2]", {"m1"}, ["a"]) is None


def test_a_new_task_is_never_offered_as_its_own_match():
    import json

    from conftest import at, make_harness
    from muninn.core.todo_match import ModelTaskMatch

    h = make_harness(at(2026, 10, 26, 9))
    asked = []

    def complete(system, prompt, *, json_mode=False):
        asked.append(json.loads(prompt))
        return json.dumps({"same": [{"new": "talk-2-task-0", "as": "talk-1-task-0"}]})

    h.cloud.up = True
    h.cloud.complete = h.local.complete = complete
    new = [{"key": "talk-2-task-0", "text": "量臥室牆寬"}]
    known = [{"key": "talk-2-task-0", "text": "量臥室牆寬"}, {"key": "talk-1-task-0", "text": "量臥室的牆寬", "status": "open"}]
    got = ModelTaskMatch(h.engine.router)(new, known)
    assert [k["key"] for k in asked[0]["known"]] == ["talk-1-task-0"]  # itself is not among what it could be
    assert got == {"talk-2-task-0": "talk-1-task-0"}
