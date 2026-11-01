"""Handoffs are rows: the web server adds them while the nightly delivery
marks them delivered from another process, and neither loses the other's."""

from __future__ import annotations

import json
from datetime import timedelta

from muninn.adapters.store_sqlite import SqliteStore
from muninn.core.handoffs import stronger

from conftest import at


def add(store, when, **fields):
    return store.add_handoff(fields, when, replaces=stronger)


def test_two_processes_do_not_lose_each_others_writes(tmp_path):
    web, cli = SqliteStore(tmp_path / "m.db"), SqliteStore(tmp_path / "m.db")
    t = at(2026, 10, 21)
    a = add(web, t, project="a", fact="f1", quote="q", item="do A", effect="changes", why="w")
    cli.set_handoff_status(a["id"], "delivered", t)  # the nightly ack, while the web server keeps adding
    b = add(web, t, project="b", fact="f2", quote="q", item=None, effect="relevant", why="")
    assert [h["id"] for h in cli.handoffs()] == [b["id"]]
    assert [h["status"] for h in web.handoffs(status=None)] == ["delivered", "pending"]


def test_same_pending_handoff_is_kept_and_only_a_stronger_effect_replaces_it(tmp_path):
    s = SqliteStore(tmp_path / "m.db")
    t = at(2026, 10, 21)
    first = add(s, t, project="a", fact="f", item="do A", effect="changes", why="1")
    assert add(s, t, project="a", fact="other", item="do A", effect="relevant", why="2") == first
    up = add(s, t, project="a", fact="f", item="do A", effect="resolves", why="3")
    assert up["id"] == first["id"] and up["effect"] == "resolves" and up["why"] == "3"
    assert s.handoffs() == [up]
    # without a to-do, the fact is what makes two the same
    x = add(s, t, project="a", fact="f", item=None, effect="relevant", why="")
    assert add(s, t, project="a", fact="f", item=None, effect="relevant", why="")["id"] == x["id"]
    assert add(s, t, project="a", fact="g", item=None, effect="relevant", why="")["id"] != x["id"]
    # once delivered, the same fact can be handed off again
    s.set_handoff_status(first["id"], "delivered", t)
    assert add(s, t, project="a", fact="f", item="do A", effect="changes", why="")["id"] != first["id"]


def test_the_json_list_is_moved_into_rows_once(tmp_path):
    old = [{"id": 1, "ts": "2026-10-01T09:00:00+08:00", "status": "delivered", "project": "a", "fact": "f", "quote": "q",
            "item": "do A", "effect": "resolves", "why": "w"},
           {"id": 3, "ts": "2026-10-02T09:00:00+08:00", "status": "pending", "project": "b", "fact": "g", "quote": None,
            "item": None, "effect": "relevant", "why": ""}]
    SqliteStore(tmp_path / "m.db").set_state("handoffs", old)
    s = SqliteStore(tmp_path / "m.db")
    assert s.handoffs(status=None) == old and s.get_state("handoffs") is None
    assert s.handoffs(since=1) == [old[1]]
    assert add(s, at(2026, 10, 21), project="c", fact="h", item=None, effect="relevant", why="")["id"] == 4
    assert SqliteStore(tmp_path / "m.db").handoffs(status=None)[:2] == old  # reopening changes nothing


def test_settled_handoffs_are_pruned_after_sixty_days():
    from test_facts import harness, run

    h = harness()
    for project in ("home-budget", "resume"):
        run(h, "x", {"domain": "jobs", "summary": f"x {project}", "effects": [], "projects": [project]})
    first, second = h.engine.handoffs()
    h.engine.set_handoff_status(first["id"], "delivered")
    h.clock.t += timedelta(days=61)
    h.engine.set_handoff_status(999, "delivered")  # no such id: nothing happens, nothing pruned
    assert len(h.store.handoffs(status=None)) == 2
    h.engine.set_handoff_status(second["id"], "cancelled")
    assert [x["id"] for x in h.store.handoffs(status=None)] == [second["id"]]  # pending ones are never pruned


def test_cli_output_keeps_its_shape(tmp_path, capsys):
    from muninn import main

    SqliteStore(tmp_path / "muninn.db").set_state("handoffs", [
        {"id": 1, "ts": "2026-10-01T09:00:00+08:00", "status": "pending", "project": "a", "fact": "f", "quote": "q",
         "item": "do A", "effect": "resolves", "why": "w"}])
    assert main.main(["--data", str(tmp_path), "handoffs", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == [{"v": 1, "id": 1, "ts": "2026-10-01T09:00:00+08:00", "status": "pending", "project": "a", "fact": "f",
                    "quote": "q", "item": "do A", "effect": "resolves", "why": "w"}]
    assert list(out[0]) == ["v", "id", "ts", "status", "project", "fact", "quote", "item", "effect", "why"]
    assert main.main(["--data", str(tmp_path), "handoffs", "--delivered", "1"]) == 0
    assert main.main(["--data", str(tmp_path), "handoffs", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_handoffs_and_case_feedback_work_without_an_engine():
    from conftest import FakeClock

    from muninn.core.cases import CaseFeedback
    from muninn.core.handoffs import Handoffs

    store, clock = SqliteStore(":memory:"), FakeClock(at(2026, 10, 21))
    cases = CaseFeedback(store, clock)
    log = Handoffs(store, clock, cases)
    cid = store.add_eval_case(clock.now(), "facts", "s", {}, "{}", "cloud", by_local=False, cloud_ok=True, json_mode=True)
    h = log.add(project="b", fact="f", item="問 B", effect="changes", why="")
    cases.link(f"handoff:{h['id']}", cid)
    cases.link("handoff:99", None)  # nothing to link
    assert log.set_status(h["id"], "cancelled") and log.pending() == []
    [fb] = store.eval_cases()[0]["feedback"]
    assert fb == {"ts": clock.now().isoformat(), "kind": "handoff_cancelled", "project": "b", "item": "問 B", "effect": "changes"}


def test_what_was_handed_to_a_project_follows_its_new_name(harness):
    h = harness
    a = h.engine.handoff_log.add(project="idea-3", item="量牆", fact="牆 3.3 m", effect="resolves")
    h.engine.handoff_log.add(project="garden", item="剪枝", fact="週六剪", effect="relevant")
    h.engine.project_renamed("idea-3", "shelf-planner")
    assert h.engine.handoff_log.get(a["id"])["project"] == "shelf-planner"
    assert {x["project"] for x in h.engine.handoffs()} == {"shelf-planner", "garden"}
