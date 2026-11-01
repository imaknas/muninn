"""What every project side must guarantee, run against each one: the external side (another
system keeps the records, posts messages to the API and pulls the handoffs) and the local side
(plain folders). A driver plays the projects for its side; the assertions are the same.

Where the two differ by design, the driver does what a real sender on that side does: on the
external side message ids are unique across projects (the sender mints them), on the local side
each folder picks its own and Muninn keeps them apart."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from conftest import ZH, at, make_harness
from starlette.testclient import TestClient

from muninn.adapters.channels.webpush import VapidKeys
from muninn.adapters.lease_runner import HttpSandboxes
from muninn.adapters.local_projects import LocalProjects
from muninn.adapters.project_records import FileProjectRecords
from muninn.adapters.web.app import create_app
from muninn.adapters.web.devices import DeviceRegistry

PROJECTS = ("garden", "thesis")
OWNERS = {"只有": "me", "agent": "agent", "待定": "pending"}  # the todo-md v1 headings, as config maps them


class FakeRunner:
    """The session runner's sandbox endpoint, as lease-runner.v2 describes it."""

    def __init__(self, token: str):
        self.token = token
        self.sandboxes: dict[str, str] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"v": 1, "error": "unauthorized"})
        body = __import__("json").loads(request.content)
        name, purpose = body["name"], body["purpose"]
        if not re.fullmatch(r"idea-[0-9]+", name):
            return httpx.Response(400, json={"v": 1, "error": "bad_name"})
        if name in self.sandboxes:
            if self.sandboxes[name] != purpose:
                return httpx.Response(409, json={"v": 1, "error": "exists", "detail": "another purpose"})
            return httpx.Response(200, json={"v": 1, "name": name, "created": False})
        self.sandboxes[name] = purpose
        return httpx.Response(201, json={"v": 1, "name": name, "created": True})


class ExternalSide:
    """Projects kept by another system: one todo.md for all, a STATE.md per project, messages
    posted to /api/messages, handoffs pulled with `muninn handoffs --json` and acknowledged."""

    def __init__(self, tmp: Path):
        self.root = tmp
        self.h = make_harness(at(2026, 10, 22, 10))
        e = self.h.engine
        e.records = FileProjectRecords(str(tmp / "todo.md"), OWNERS, str(tmp / "{name}" / "STATE.md"), None)
        keys = VapidKeys(tmp / "secrets" / "vapid.pem")
        keys.ensure()
        self.client = TestClient(create_app(e, keys, "tok", DeviceRegistry(tmp / "dev.db"), messages=e.messages),
                                 base_url="https://testserver")
        self._todos: dict[str, dict[str, list[str]]] = {}
        (tmp / "secrets" / "runner-token").write_text("rt\n")
        e.sandboxes = HttpSandboxes("http://runner", tmp / "secrets" / "runner-token",
                                    httpx.Client(transport=httpx.MockTransport(FakeRunner("rt"))))

    def own_id(self, project: str, mid: str) -> str:
        return f"{project}-{mid}"  # the sender mints ids unique across projects

    def send(self, project: str, *messages: dict[str, Any]) -> int:
        body = [{**m, "project": project, "id": self.own_id(project, m["id"]),
                 **({"task": self.own_id(project, m["task"])} if m.get("task") else {})} for m in messages]
        return self.client.post("/api/messages", json={"messages": body}, headers={"Authorization": "Bearer tok"}).status_code

    def write_todos(self, project: str, **by_owner: list[str]) -> None:
        self._todos[project] = by_owner
        heading = {"me": "## 只有 Ada 能做", "pending": "## 待定(需要先做決定)", "agent": "## agent 可以做"}
        lines = ["<!-- contract: todo-md v1 -->", "# 跨專案待辦"]
        for owner in ("me", "pending", "agent"):
            lines.append(heading[owner])
            for p, items in self._todos.items():
                if items.get(owner):
                    lines += [f"### {p}", *(f"- [ ] {x}" for x in items[owner])]
        (self.root / "todo.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def write_state(self, project: str, text: str) -> None:
        (self.root / project).mkdir(exist_ok=True)
        (self.root / project / "STATE.md").write_text(text, encoding="utf-8")

    def tick(self) -> None:
        self.h.engine.tick()

    def received(self, project: str) -> list[dict[str, Any]]:
        """What the delivery pulls for this project now (and then acknowledges)."""
        got = [x for x in self.h.engine.handoffs() if x["project"] == project]
        for x in got:
            self.h.engine.set_handoff_status(x["id"], "delivered")
        return [{**x, "reply_to": x.get("reply_to")} for x in got]


class LocalSide:
    """Projects as folders: TODO.md, STATE.md, .muninn/outbox.yaml in, .muninn/inbox.md out."""

    def __init__(self, tmp: Path):
        self.root = tmp
        for p in PROJECTS:
            (tmp / p / ".muninn").mkdir(parents=True)
        (tmp / "sandboxes").mkdir()
        side = LocalProjects([tmp / p for p in PROJECTS], tmp / "sandboxes", words=ZH)
        self.h = make_harness(at(2026, 10, 22, 10))
        e = self.h.engine
        e.records, e.project_inbox, e.handoff_outbox, e.sandboxes = side, side, side, side
        self._read: dict[str, int] = {}

    def own_id(self, project: str, mid: str) -> str:
        return mid  # each folder picks its own

    def send(self, project: str, *messages: dict[str, Any]) -> int:
        outbox = self.root / project / ".muninn" / "outbox.yaml"
        with outbox.open("a", encoding="utf-8") as f:
            for m in messages:
                f.write("- {" + ", ".join(f"{k}: {v!r}" if isinstance(v, str) else f"{k}: {v}" for k, v in m.items()) + "}\n")
        self.tick()
        return 202

    def write_todos(self, project: str, **by_owner: list[str]) -> None:
        heading = {"me": "## 只有我能做", "pending": "## 待定", "agent": "## agent 可以做"}
        lines = []
        for owner in ("me", "pending", "agent"):
            if by_owner.get(owner):
                lines += [heading[owner], *(f"- [ ] {x}" for x in by_owner[owner])]
        (self.root / project / "TODO.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def write_state(self, project: str, text: str) -> None:
        (self.root / project / "STATE.md").write_text(text, encoding="utf-8")

    def tick(self) -> None:
        self.h.engine.tick()

    def received(self, project: str) -> list[dict[str, Any]]:
        """The inbox entries written since the last look, as the project's agent reads them."""
        inbox = self.root / project / ".muninn" / "inbox.md"
        text = inbox.read_text(encoding="utf-8") if inbox.is_file() else ""
        entries = [e for e in text.split("## ")[1:]]
        new, self._read[project] = entries[self._read.get(project, 0):], len(entries)
        out = []
        for entry in new:
            head, *fields = entry.strip().splitlines()
            got = {"id": int(head.rsplit("#", 1)[1]), "effect": head.split(" · ")[1]}
            labels = {"關於": "item", "要做的": "fact", "他的原話": "quote", "回覆的訊息": "reply_to", "已經處理過的任務": "task"}
            for line in fields:
                if m := re.match(r"- (.+?):(.*)", line):
                    got[labels.get(m.group(1), m.group(1))] = m.group(2)
            out.append(got)
        return out


@pytest.fixture(params=["external", "local"])
def side(request, tmp_path):
    return {"external": ExternalSide, "local": LocalSide}[request.param](tmp_path)


def test_a_question_a_project_asks_arrives_once(side):
    side.send("garden", {"id": "q1", "type": "question", "text": "Red or white roses?"})
    side.send("garden", {"id": "q1", "type": "question", "text": "Red or white roses?"})  # sent again
    assert [q["text"] for q in side.h.engine.messages.open_questions()] == ["Red or white roses?"]


def test_a_task_a_project_registers_is_closed_by_its_done(side):
    side.h.engine.tasks.grant_claims_mandate("專案說做完了就記成做完")  # his standing permission (else he is asked)
    side.send("garden", {"id": "t1", "type": "task", "text": "Order soil for the rose bed"})
    assert [t["title"] for t in side.h.engine.tasks.open()] == ["Order soil for the rose bed"]
    side.send("garden", {"id": "d1", "type": "done", "task": "t1", "text": "ordered"})
    assert side.h.engine.tasks.open() == []


def test_a_malformed_message_never_comes_in(side):
    side.send("garden", {"id": "bad", "type": "shout", "text": "hi"})
    side.tick()
    assert side.h.engine.messages.open_questions() == [] and side.h.engine.tasks.open() == []


def test_a_handoff_reaches_its_own_project_once(side):
    e = side.h.engine
    hid = e.handoff_log.add(project="garden", item="Order soil", fact="soil ordered", effect="resolves", quote="土訂好了")["id"]
    side.tick()
    [got] = side.received("garden")
    assert got["id"] == hid and got["item"] == "Order soil" and got["quote"] == "土訂好了"
    assert side.received("thesis") == []  # another project never sees it
    side.tick()
    assert side.received("garden") == []  # and it is not handed out again
    assert e.handoff_log.pending() == []


def test_the_answer_to_a_question_goes_back_with_the_projects_own_id(side):
    side.send("garden", {"id": "q1", "type": "question", "text": "Red or white roses?"})
    [q] = side.h.engine.messages.open_questions()
    side.h.engine.messages.answer(q["id"], "紅的")
    side.tick()
    [got] = side.received("garden")
    assert got["effect"] == "answers" and got["reply_to"] == side.own_id("garden", "q1")


def test_to_dos_and_state_read_alike(side):
    side.write_todos("garden", me=["Water the beds"], agent=["Sort the seed list"], pending=["Pick a rose colour"])
    side.write_state("garden", "Roses planted on the east side.")
    todos = {(t.project, t.owner, t.text) for t in side.h.engine.records.todos()}
    assert todos == {("garden", "me", "Water the beds"), ("garden", "agent", "Sort the seed list"),
                     ("garden", "pending", "Pick a rose colour")}
    assert side.h.engine.records.state("garden") == "Roses planted on the east side."


def test_a_sandbox_opened_again_for_the_same_idea_is_the_same_one_and_a_name_taken_is_refused(side):
    from muninn.core.ports import SandboxRefused

    sandboxes = side.h.engine.sandboxes
    name = sandboxes.create("idea-7", "做一個澆花提醒")
    assert name and sandboxes.create("idea-7", "做一個澆花提醒") == name  # a retry is safe
    with pytest.raises(SandboxRefused) as refused:
        sandboxes.create("idea-7", "別的事")
    assert refused.value.kind == "taken"
