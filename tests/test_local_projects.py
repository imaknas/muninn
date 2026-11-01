from __future__ import annotations

from pathlib import Path

import pytest
from conftest import ZH, at, make_harness

from muninn.core.ports import SandboxRefused
from muninn.adapters.local_projects import LocalProjects


def project(root: Path, name: str, todo: str = "", state: str | None = None, outbox: str | None = None) -> Path:
    p = root / name
    (p / ".muninn").mkdir(parents=True)
    if todo:
        (p / "TODO.md").write_text(todo, encoding="utf-8")
    if state is not None:
        (p / "STATE.md").write_text(state, encoding="utf-8")
    if outbox is not None:
        (p / ".muninn" / "outbox.yaml").write_text(outbox, encoding="utf-8")
    return p


def test_folders_listed_are_the_projects_with_their_todos_and_state(tmp_path):
    garden = project(tmp_path, "garden", "- [ ] Order soil\n- [x] Buy gloves\n## agent can do\n- [ ] Sort seed list\n## 待定\n- [ ] Pick a rose colour\n",
                     state="Roses planted on the east side.")
    project(tmp_path, "not-listed", "- [ ] Secret\n")
    side = LocalProjects([garden], words=ZH)
    assert side.projects() == {"garden"}  # only what the user listed
    assert [(t.owner, t.text) for t in side.todos()] == [("me", "Order soil"), ("agent", "Sort seed list"), ("pending", "Pick a rose colour")]
    assert side.state("garden") == "Roses planted on the east side." and side.state("not-listed") is None
    with pytest.raises(ValueError):
        LocalProjects([garden, tmp_path / "x" / "garden"], words=ZH)  # a project is its folder's name


def test_handoffs_are_written_once_to_the_projects_inbox(tmp_path):
    garden = project(tmp_path, "garden")
    side = LocalProjects([garden], words=ZH)
    h = {"v": 1, "id": 7, "ts": "2026-10-22T10:00:00+08:00", "status": "pending", "project": "garden", "effect": "answers",
         "item": "Red or white roses?", "fact": "Red", "quote": "紅的", "reply_to": "garden:garden-q1"}
    other = {**h, "id": 8, "project": "elsewhere"}
    assert side.deliver([h, other]) == [7]  # a project not listed keeps its handoff pending
    assert side.deliver([h]) == [7]  # already there: counted, not written again
    inbox = (garden / ".muninn" / "inbox.md").read_text(encoding="utf-8")
    assert inbox.count("· #7") == 1 and "- 他的原話:紅的" in inbox and "- 回覆的訊息:garden-q1" in inbox


def test_the_projects_outbox_comes_in_and_a_bad_entry_is_left_out(tmp_path):
    garden = project(tmp_path, "garden", outbox="- {id: garden-q1, type: question, text: Red or white roses?}\n"
                                                 "- {id: garden-bad, type: shout, text: hi}\n"
                                                 "- {id: garden-t1, type: task, text: Order soil, due: '2026-10-30'}\n")
    got = LocalProjects([garden], words=ZH).collect()
    assert [(m["id"], m["project"]) for m in got] == [("garden:garden-q1", "garden"), ("garden:garden-t1", "garden")]


def test_a_sandbox_is_a_new_project_with_its_purpose(tmp_path):
    side = LocalProjects([], tmp_path / "ideas", words=ZH)
    name = side.create("室內 3D/空間規劃", "從照片生 3D 模型,看能不能做空間規劃")
    assert name and name in side.projects()
    assert "從照片生 3D" in (tmp_path / "ideas" / name / "README.md").read_text(encoding="utf-8")
    assert side.create("室內 3D/空間規劃", "從照片生 3D 模型,看能不能做空間規劃") == name  # a retry: the same folder
    with pytest.raises(SandboxRefused) as refused:  # the name means another sandbox already
        side.create("室內 3D/空間規劃", "something else")
    assert refused.value.kind == "taken"
    assert LocalProjects([], words=ZH).create("x", "y") is None  # no sandbox root: this side opens none


def test_a_muninn_on_folders_reads_and_answers_its_projects_on_a_tick(tmp_path):
    garden = project(tmp_path, "garden", "- [ ] Water the beds\n", outbox="- {id: garden-q1, type: question, text: Red or white roses?}\n")
    side = LocalProjects([garden], words=ZH)
    h = make_harness(at(2026, 10, 22, 10))
    e = h.engine
    e.records, e.project_inbox, e.handoff_outbox = side, side, side
    hid = e.handoff_log.add(project="garden", item="Order soil", fact="soil ordered", effect="resolves")["id"]
    e.tick()
    assert [q["text"] for q in e.messages.open_questions()] == ["Red or white roses?"]  # the outbox came in
    assert f"· #{hid}" in (garden / ".muninn" / "inbox.md").read_text(encoding="utf-8")  # the handoff went out
    assert e.handoff_log.pending() == []


def test_config_chooses_where_projects_live(tmp_path):
    from muninn.adapters.project_records import FileProjectRecords
    from muninn.config import Config
    from muninn.main import project_side

    (tmp_path / "config.yaml").write_text("projects: {side: local, folders: [~/nowhere], sandbox_root: null}\n", encoding="utf-8")
    local = project_side(Config.load(str(tmp_path)), ZH)
    assert isinstance(local["records"], LocalProjects) and local["project_inbox"] is local["handoff_outbox"] is local["records"]
    (tmp_path / "config.yaml").write_text("{}\n", encoding="utf-8")
    assert isinstance(project_side(Config.load(str(tmp_path)), ZH)["records"], LocalProjects)  # the default: plain folders
    (tmp_path / "config.yaml").write_text("projects: {side: external}\n", encoding="utf-8")
    assert isinstance(project_side(Config.load(str(tmp_path)), ZH)["records"], FileProjectRecords)  # another system keeps them
    (tmp_path / "config.yaml").write_text("projects: {side: cloudy}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        project_side(Config.load(str(tmp_path)), ZH)


def test_two_projects_may_use_the_same_ids(tmp_path):
    garden = project(tmp_path, "garden", outbox="- {id: q1, type: question, text: Red or white roses?}\n"
                                                 "- {id: t1, type: task, text: Order soil}\n")
    thesis = project(tmp_path, "thesis", outbox="- {id: q1, type: question, text: Which chapter first?}\n")
    side = LocalProjects([garden, thesis], words=ZH)
    h = make_harness(at(2026, 10, 22, 10))
    e = h.engine
    e.records, e.project_inbox, e.handoff_outbox = side, side, side
    e.tasks.grant_claims_mandate("專案說做完了就記成做完")
    e.tick()
    assert sorted(q["text"] for q in e.messages.open_questions()) == ["Red or white roses?", "Which chapter first?"]  # both arrive
    (garden / ".muninn" / "outbox.yaml").write_text("- {id: done-t1, type: done, task: t1, text: ordered}\n", encoding="utf-8")
    e.tick()
    assert e.tasks.get("garden:t1")["status"] == "done"  # its own task id finds its task
