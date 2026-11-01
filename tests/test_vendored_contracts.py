"""Muninn reads three formats another system produces, kept here as copies
(src/muninn/contracts/vendor/records/). The producer checks nightly that the copies still
match its originals; these tests check that Muninn's readers agree with the
copies, using the producer's own example files. `{owner}` in the contracts is
the user's label from config; the examples use 「我」."""

from __future__ import annotations

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from muninn.adapters.project_records import parse_todos, unowned_headings
from muninn.package import CONTRACTS

VENDOR = CONTRACTS / "vendor" / "records"
OWNER = "我"


def blocks(contract: str, kind: str) -> dict[str, str]:
    """The fenced ```<kind> <name>``` blocks of a markdown contract, {owner} filled in."""
    text = (VENDOR / contract).read_text()
    found = re.findall(rf"```{kind} (\S+)\n(.*?)```", text, re.S)
    fill = re.escape(OWNER) if kind == "regex" else OWNER
    return {name: body.strip().replace("{owner}", fill) for name, body in found}


def test_todo_headings_map_to_muninn_owners_and_items_parse():
    headings = blocks("todo-md.v1.md", "heading")["owner"].splitlines()
    assert [h[:3] for h in headings] == ["## "] * 3
    # the mapping Muninn's config gives for this producer, built from the contract's own headings
    owners = dict(zip((h[3:] for h in headings), ("me", "pending", "agent")))
    example = (VENDOR / "fixtures" / "todo-md.v1.example.md").read_text()
    assert example.splitlines()[0] == "<!-- contract: todo-md v1 -->"
    assert unowned_headings(example, owners) == []
    todos = parse_todos(example, owners)
    assert [(t.owner, t.project, t.text) for t in todos] == [
        ("me", "example-project", "決定要不要開第二個帳號"),
        ("agent", "example-project", "補測試"),
        ("agent", "example-project", "更新 README"),
    ]  # the 「> 另有 N 項已登記」 note and 「(無)」 are not items


def test_todo_item_rule_agrees_with_muninn_on_the_example():
    rules = blocks("todo-md.v1.md", "regex")
    example = (VENDOR / "fixtures" / "todo-md.v1.example.md").read_text().splitlines()
    by_contract = [m.group(1) for line in example if (m := re.match(rules["item"], line))]
    owners = {h[3:]: o for h, o in zip(blocks("todo-md.v1.md", "heading")["owner"].splitlines(), ("me", "pending", "agent"))}
    assert by_contract == [t.text for t in parse_todos("\n".join(example), owners)]


def test_state_todo_rules_read_the_example():
    rules = {k: re.compile(v) for k, v in blocks("state-todo.v1.md", "regex").items()}
    lines = (VENDOR / "fixtures" / "state-todo.v1.example.md").read_text().splitlines()
    items = [m.groups() for line in lines if (m := rules["item"].match(line))]
    assert [owner for owner, _ in items] == [OWNER, OWNER, "agent", "待定"]
    keys = [m.group(1) for line in lines if (m := rules["key"].search(line))]
    assert keys == ["task-example-project-0123456789", "task-example-project-abcdef0123"]


def test_weekly_stats_keep_what_muninn_reads():
    schema = json.loads((VENDOR / "reflect-week.v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    # core/reflect.py passes on stats["totals"] and stats["rhythm"]: the producer must keep them required
    assert {"totals", "rhythm"} <= set(schema["required"])


def _defs(name: str):
    import json as _json
    from jsonschema import Draft202012Validator as V

    path = VENDOR / name if (VENDOR / name).exists() else VENDOR.parent.parent / name
    s = _json.loads(path.read_text())
    V.check_schema(s)
    return {k: V({**v, "$defs": s.get("$defs", {})}) for k, v in s["$defs"].items()}


def test_what_muninn_sends_the_session_runner_matches_its_contract():
    """The runner adapter's request bodies, as built by HttpSessionRunner."""
    d = _defs("lease-runner.v2.schema.json")
    assert not list(d["create"].iter_errors({"v": 2, "project": "garden", "mode": "discuss",
                                             "session_id": "1c9e4d2a-0000-4000-8000-000000000000", "thread": "abc", "resume": False}))
    assert not list(d["message"].iter_errors({"v": 2, "text": "hi", "client_msg_id": "m1"}))
    assert not list(d["detach"].iter_errors({"v": 2, "reason": "user"}))
    # what Muninn reads back: steps by kind, quota windows by label, a failed resume said, the terminal's hint
    assert {"attachable", "no_session_domains"} <= set(d["policy"].schema["required"])
    assert {"command", "hint"} <= set(d["terminal"].schema["required"])
    events = set(d["event"].schema["properties"]["type"]["enum"])
    assert {"state", "assistant", "tool_use", "turn_end", "quota", "idle_warning", "handover", "error"} <= events
    assert {"kind", "summary"} <= set(d["tool_use_event"].schema["required"])
    assert {"id", "label", "used"} <= set(d["quota"].schema["properties"]["windows"]["items"]["required"])
    assert "resumed" in d["state_event"].schema["properties"] and "agent_login" in d["error"].schema["properties"]["error"]["enum"]


def test_the_handover_has_what_the_summary_card_shows():
    import json as _json

    s = _json.loads((VENDOR / "lease-handover.v1.schema.json").read_text())
    assert {"summary", "decisions", "open_questions", "tasks_for_owner", "fallback"} <= set(s["properties"])


def test_muninns_permission_request_contract_v2_says_what_is_asked_by_kind():
    d = _defs("permission-requests.v2.schema.json")  # Muninn's own, in src/muninn/contracts/
    assert not list(d["request"].iter_errors({"v": 2, "id": "L:t", "lease": "L", "project": "garden", "kind": "open_web",
                                              "summary": "打開 arxiv 的論文頁", "target": "https://arxiv.org/abs/1"}))
    assert not list(d["request"].iter_errors({"v": 2, "id": "L:q", "kind": "ask_user", "summary": "問你要哪個", "question": {
        "questions": [{"question": "要測哪些房間?", "multi": True, "options": [{"label": "臥室", "description": "有實測尺寸"}, {"label": "客廳"}]}]}}))
    assert list(d["request"].iter_errors({"v": 2, "id": "L:t", "kind": "WebFetch", "summary": "x"}))  # the agent's own name: never
    assert not list(d["decision"].iter_errors({"v": 2, "decision": "allow", "answers": {"要測哪些房間?": ["臥室", "客廳"]}}))
    assert not list(d["pending"].iter_errors({"v": 2, "id": "L:t", "pending": True}))


