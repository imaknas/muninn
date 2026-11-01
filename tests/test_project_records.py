"""Contract with the files another system writes for each project.

The fixtures follow the real files' layout (headings, sections, item lines)
with made-up items. If the producer changes its headings or layout, update the
fixtures to match: a test failing here means Muninn would misread the live
files the same way.
"""

from __future__ import annotations

import logging
from pathlib import Path

from muninn.adapters.project_records import FileProjectRecords, owners_match, parse_todos, unowned_headings

FIXTURES = Path(__file__).parent / "fixtures" / "project_records"
TODOS = FIXTURES / "todo.md"
STATES = str(FIXTURES / "states" / "{name}" / "STATE.md")
# an owner mapping shaped like the deployed config's for this producer
OWNERS = {"只有我能做": "me", "agent 可以做": "agent", "待定": "pending"}


def test_every_heading_in_the_todo_file_maps_to_a_configured_owner():
    text = TODOS.read_text()
    assert unowned_headings(text, OWNERS) == []
    assert owners_match(text, OWNERS)
    todos = parse_todos(text, OWNERS)
    assert {t.owner for t in todos} == {"me", "agent", "pending"}
    assert {t.project for t in todos} == {"alpha", "beta", "gamma"}
    assert len(todos) == 12  # the header's count; the checked item is left out


def test_renamed_headings_are_caught_and_logged(tmp_path, caplog):
    renamed = TODOS.read_text().replace("只有我能做", "Only me").replace("agent 可以做", "Agents").replace("待定", "Undecided")
    assert not owners_match(renamed, OWNERS)
    assert len(unowned_headings(renamed, OWNERS)) == 3
    p = tmp_path / "todo.md"
    p.write_text(renamed)
    r = FileProjectRecords(str(p), OWNERS, None, None)
    with caplog.at_level(logging.WARNING, logger="muninn.adapters.project_records"):
        assert {t.owner for t in r.todos()} == {"pending"}
        r.todos()
    assert sum("no heading matches" in m for m in caplog.messages) == 1  # once, not on every read
    p.write_text(TODOS.read_text())
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="muninn.adapters.project_records"):
        assert {t.owner for t in r.todos()} == {"me", "agent", "pending"}
    assert caplog.messages == []


def test_state_files_are_found_and_read():
    r = FileProjectRecords(str(TODOS), OWNERS, STATES, None)
    assert "alpha" in r.projects() and {"beta", "gamma"} <= r.projects()
    state = r.state("alpha")
    assert state and state.startswith("# STATE") and "## 待辦" in state
    assert r.state("beta") is None and r.state("../alpha") is None


def test_the_registered_summary_line_is_not_a_todo():
    from muninn.adapters.project_records import parse_todos

    text = "## 只有我能做\n> 另有 3 項已登記在 Muninn(STATE.md 行尾標「等 Ada:<key>」),以 Muninn 為準,這裡不列。\n\n### garden\n- [ ] Pick tiles\n"
    assert [t.text for t in parse_todos(text, {"只有我能做": "me"})] == ["Pick tiles"]


def test_registered_lines_follow_the_owner_label_from_the_contract_fixture():
    from pathlib import Path

    from muninn.core.open_items import registered_marker

    lines = (Path(__file__).parent.parent / "contracts/vendor/records/fixtures/state-todo.v1.example.md").read_text().splitlines()
    keyed = [line for line in lines if "task-example" in line]
    assert len(keyed) == 2 and all(registered_marker("我")(line) for line in keyed)
    assert not any(registered_marker("Ada")(line) for line in keyed)
    assert registered_marker("Ada")("- [ ] 〔Ada〕x(等 Ada：task-a-0123456789)")  # full-width colon too
