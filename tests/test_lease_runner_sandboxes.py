from __future__ import annotations

import json

import httpx
import pytest

from conftest import at, make_harness
from muninn.adapters.lease_runner import PURPOSE_MAX, HttpSandboxes
from muninn.core.ports import SandboxRefused


def sandboxes(tmp_path, reply, seen=None):
    (tmp_path / "token").write_text("rt\n")

    def handle(request):
        if seen is not None:
            seen.append(request)
        return reply(request)

    return HttpSandboxes("http://runner/", tmp_path / "token", httpx.Client(transport=httpx.MockTransport(handle)))


def test_a_sandbox_is_asked_of_the_runner_with_its_token_and_the_purpose_cut_to_the_contracts_limit(tmp_path):
    seen = []
    s = sandboxes(tmp_path, lambda r: httpx.Response(201, json={"v": 1, "name": "idea-3", "created": True}), seen)
    assert s.create("idea-3", "長" * 600) == "idea-3"
    [r] = seen
    body = json.loads(r.content)
    assert r.url == "http://runner/v1/sandboxes" and r.headers["Authorization"] == "Bearer rt"
    assert body == {"v": 2, "name": "idea-3", "purpose": "長" * PURPOSE_MAX}


@pytest.mark.parametrize("status,body,kind", [
    (409, {"v": 1, "error": "exists"}, "taken"),
    (429, {"v": 1, "error": "too_many", "limit": 20, "current": 20}, "too_many"),
    (400, {"v": 1, "error": "bad_name"}, "bad_name"),
    (500, {"v": 1, "error": "create_failed", "detail": "disk full"}, "failed"),
])
def test_what_the_runner_refuses_says_why(tmp_path, status, body, kind):
    s = sandboxes(tmp_path, lambda r: httpx.Response(status, json=body))
    with pytest.raises(SandboxRefused) as refused:
        s.create("idea-3", "x")
    assert refused.value.kind == kind
    if kind == "too_many":
        assert (refused.value.limit, refused.value.current) == (20, 20)


def test_a_runner_out_of_reach_is_unreachable(tmp_path):
    def down(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(SandboxRefused) as refused:
        sandboxes(tmp_path, down).create("idea-3", "x")
    assert refused.value.kind == "unreachable"


def test_too_many_sandboxes_is_said_plainly_when_exploring():
    h = make_harness(at(2026, 10, 24, 22))

    class Full:
        def create(self, name, purpose):
            raise SandboxRefused("too_many", limit=20, current=20)

    h.engine.sandboxes = Full()
    n = h.engine.ideas.add("做一個澆花提醒")["n"]
    with pytest.raises(ValueError, match="已經開了 20 個,上限 20 個"):
        h.engine.ideas.explore(n)
    assert h.engine.ideas.get(n).get("sandbox") is None


def test_making_a_sandbox_a_project_asks_the_runner_and_matches_the_contract(tmp_path):
    from test_vendored_contracts import _defs

    seen = []
    s = sandboxes(tmp_path, lambda r: httpx.Response(200, json={"v": 1, "name": "shelf-planner", "origin": "git@x:o/shelf-planner.git",
                                                                "upgraded": True}), seen)
    assert s.upgrade("idea-3", "shelf-planner") == "shelf-planner"
    assert s.upgrade("idea-3", None) == "shelf-planner"  # asked again: the name it has now
    first, again = (json.loads(r.content) for r in seen)
    assert seen[0].url == "http://runner/v1/sandboxes/idea-3/upgrade" and seen[0].headers["Authorization"] == "Bearer rt"
    assert first == {"v": 2, "rename": "shelf-planner"} and again == {"v": 2}
    d = _defs("lease-runner.v2.schema.json")
    assert not list(d["sandbox_upgrade"].iter_errors(first)) and not list(d["sandbox_upgrade"].iter_errors(again))
    assert {"name", "origin", "upgraded"} <= set(d["sandbox_upgraded"].schema["required"])


@pytest.mark.parametrize("status,body,kind", [
    (409, {"v": 1, "error": "name_taken", "detail": "garden exists"}, "taken"),
    (409, {"v": 1, "error": "dirty", "detail": "README.md"}, "dirty"),
    (409, {"v": 1, "error": "not_idea"}, "failed"),
    (404, {"v": 1, "error": "not_found"}, "failed"),
    (500, {"v": 1, "error": "upgrade_failed", "detail": "gh: auth"}, "failed"),
])
def test_what_the_runner_refuses_when_making_a_project_says_why(tmp_path, status, body, kind):
    s = sandboxes(tmp_path, lambda r: httpx.Response(status, json=body))
    with pytest.raises(SandboxRefused) as e:
        s.upgrade("idea-3", "garden")
    assert e.value.kind == kind and (body.get("detail") or body["error"]) in str(e.value)


def test_renaming_asks_the_runner_and_matches_the_contract(tmp_path):
    from test_vendored_contracts import _defs

    seen = []
    s = sandboxes(tmp_path, lambda r: httpx.Response(200, json={"v": 2, "name": "sofa-fit", "origin": "(native)", "renamed": True}), seen)
    assert s.rename("idea-3", "sofa-fit") == "sofa-fit"
    [r] = seen
    body = json.loads(r.content)
    assert r.url == "http://runner/v1/projects/idea-3/rename" and r.headers["Authorization"] == "Bearer rt"
    assert body == {"v": 2, "to": "sofa-fit"}
    d = _defs("lease-runner.v2.schema.json")
    assert not list(d["project_rename"].iter_errors(body))
    assert {"v", "name", "renamed"} <= set(d["project_renamed"].schema["required"])


@pytest.mark.parametrize("status,body,kind", [
    (409, {"v": 2, "error": "busy", "detail": "正在回答"}, "busy"),
    (409, {"v": 2, "error": "name_taken", "detail": "sofa-fit exists"}, "taken"),
    (400, {"v": 2, "error": "bad_name"}, "bad_name"),
    (404, {"v": 2, "error": "not_found"}, "failed"),
    (500, {"v": 2, "error": "rename_failed", "detail": "gh: auth"}, "failed"),
])
def test_what_the_runner_refuses_when_renaming_says_why(tmp_path, status, body, kind):
    s = sandboxes(tmp_path, lambda r: httpx.Response(status, json=body))
    with pytest.raises(SandboxRefused) as e:
        s.rename("idea-3", "sofa-fit")
    assert e.value.kind == kind and (body.get("detail") or body["error"]) in str(e.value)


def test_a_runner_that_does_not_rename_yet_is_not_an_error(tmp_path):
    s = sandboxes(tmp_path, lambda r: httpx.Response(404, text="Not Found"))
    assert s.rename("idea-3", "sofa-fit") is None
