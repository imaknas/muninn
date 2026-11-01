"""What the user asks of Muninn itself: kept with its conversation (and the deep talk's content that round carried),
and live only once the running Muninn contains its commit, said once in that conversation and undoable; a rollback
that takes the commit away says so there; an install that cannot tell its version marks nothing live and says why."""

from __future__ import annotations

import subprocess
from pathlib import Path

from muninn.adapters.running_version import GitVersion
from muninn.core.ports import UnknownVersion
from muninn.core.requests import KEY, Requests

from conftest import ZH, at, make_harness


class FakeVersion:
    unknown = None

    def __init__(self, *commits: str, lines: list[str] | None = None):
        self.commits = set(commits)
        self.lines = list(lines or [])

    def head(self):
        return sorted(self.commits)[-1] if self.commits else None

    def contains(self, commit):
        return True if commit in self.commits else None if commit.startswith("old") else False

    def deploy_lines(self):
        return list(self.lines)


def run(fn):
    return fn()


def turns(h, tid):
    return [t for t in (h.engine.conversation().thread(tid) or {}).get("turns") or [] if t["role"] == "muninn"]


def asked(h, text, thread="t1", **plan):
    h.store.save_thread(thread, {"about": None, "ts": "2026-10-25T09:00", "turns": []})
    r = h.engine.commands().execute({"actions": [{"tool": "request_feature", "args": {"text": text}}], "reply": "", "thread": thread, **plan})
    return r.outcomes[0].text


def test_a_request_keeps_its_conversation_and_the_deep_talk_that_round_carried():
    h = make_harness(at(2026, 10, 25, 10))
    assert asked(h, "想要每週的花園澆水摘要").startswith("記到需求清單了")
    asked(h, "深談裡的草圖要能直接看", thread="t2", local_only=True, via=["lease:L1"])
    plain, talk = h.engine.feature_requests()
    assert (plain["id"], plain["thread"], plain["via"], plain["done"]) == (1, "t1", [], False)
    assert (talk["id"], talk["thread"], talk["via"]) == (2, "t2", ["lease:L1"])
    assert {"commit", "live", "handoff"} <= set(talk) and talk["commit"] is None


def test_live_once_the_running_version_contains_its_commit_said_once_even_after_a_restart():
    h = make_harness(at(2026, 10, 25, 10))
    asked(h, "想要每週的花園澆水摘要")
    asked(h, "提醒要能延後十分鐘", thread="t2")
    reqs = h.store.get_state(KEY)
    reqs[0]["commit"], reqs[1]["commit"] = "c0ffee1", "beef002"
    h.store.set_state(KEY, reqs)
    h.engine.requests.version = FakeVersion("c0ffee1")
    assert h.engine.check_live(run) == 1
    assert [r["text"] for r in h.engine.feature_requests()] == ["提醒要能延後十分鐘"]  # the one not running yet stays open
    live = next(r for r in h.store.get_state(KEY) if r["id"] == 1)
    assert live["done"] and live["live_by"] == "commit" and live["live"]
    assert [t["text"] for t in turns(h, "t1")][-1] == "上線了:「想要每週的花園澆水摘要」"
    assert turns(h, "t2") == []  # nothing said where it is not live
    assert h.engine.check_live(run) == 0
    restarted = Requests(h.store, h.clock, h.engine.gate, FakeVersion("c0ffee1"), speak=lambda *a: (_ for _ in ()).throw(AssertionError(a)), words=ZH)
    assert restarted.check(run) == 0
    assert [t["text"] for t in turns(h, "t1")].count("上線了:「想要每週的花園澆水摘要」") == 1


def test_undone_it_is_open_again_and_that_commit_no_longer_counts():
    h = make_harness(at(2026, 10, 25, 10))
    asked(h, "想要每週的花園澆水摘要")
    reqs = h.store.get_state(KEY)
    reqs[0]["commit"] = "c0ffee1"
    h.store.set_state(KEY, reqs)
    h.engine.requests.version = FakeVersion("c0ffee1")
    h.engine.check_live(run)
    [done] = [a for a in h.engine.undoable_actions() if "已上線" in a.text]
    assert "依 commit c0ffee1 認定" in done.text
    h.engine.conversation().act("t1", 0)  # 「還沒好」, offered with what was said
    assert [r["text"] for r in h.engine.feature_requests()] == ["想要每週的花園澆水摘要"]
    assert h.engine.check_live(run) == 0 and h.engine.feature_requests()  # not marked live again by the same commit


def test_a_rollback_that_took_a_live_commit_away_says_so_there_once_and_others_say_nothing():
    h = make_harness(at(2026, 10, 25, 10))
    asked(h, "想要每週的花園澆水摘要")
    reqs = h.store.get_state(KEY)
    reqs[0]["commit"] = "c0ffee1"
    h.store.set_state(KEY, reqs)
    version = FakeVersion("c0ffee1", lines=["2026-10-25 10:00 aaa0001 -> c0ffee1"])
    h.engine.requests.version = version
    h.engine.check_live(run)
    before = len(turns(h, "t1"))
    version.lines.append("2026-10-25 10:05 rollback ddd0004 -> c0ffee1: 首頁打不開")  # c0ffee1 still runs: nothing to say
    assert h.engine.check_live(run) == 0 and len(turns(h, "t1")) == before
    version.commits = {"aaa0001"}
    version.lines.append("2026-10-25 11:00 rollback c0ffee1 -> aaa0001: 首頁打不開")
    assert h.engine.check_live(run) == 1
    assert turns(h, "t1")[-1]["text"] == "上線檢查沒過(首頁打不開),已經退回上一版:「想要每週的花園澆水摘要」還沒上線。"
    assert [r["text"] for r in h.engine.feature_requests()] == ["想要每週的花園澆水摘要"]
    assert h.engine.check_live(run) == 0 and len(turns(h, "t1")) == before + 1  # each line acted on once
    version.commits = {"aaa0001", "c0ffee1"}  # deployed again: live again, said again
    assert h.engine.check_live(run) == 1 and turns(h, "t1")[-1]["text"].startswith("上線了")


def test_a_request_live_before_the_history_was_started_afresh_is_not_taken_back_by_a_later_rollback():
    h = make_harness(at(2026, 10, 25, 10))
    asked(h, "想要每週的花園澆水摘要")
    reqs = h.store.get_state(KEY)
    reqs[0]["commit"] = "old0001"  # a commit of the archived history: the running one does not know it
    reqs[0]["done"], reqs[0]["live_by"] = True, "commit"
    h.store.set_state(KEY, reqs)
    version = FakeVersion("new0001", lines=["2026-11-02 11:00 rollback new0002 -> new0001: 首頁打不開"])
    h.engine.requests.version = version
    before = len(turns(h, "t1"))
    assert h.engine.check_live(run) == 0 and len(turns(h, "t1")) == before  # not said to be rolled back
    assert h.engine.feature_requests() == []  # still live


def test_an_install_that_cannot_tell_its_version_marks_nothing_and_says_why(web):
    h, client, devices = web
    assert client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "iPhone"}).status_code == 200
    asked(h, "想要每週的花園澆水摘要")
    reqs = h.store.get_state(KEY)
    reqs[0]["commit"] = "c0ffee1"
    h.store.set_state(KEY, reqs)
    h.engine.requests.version = UnknownVersion("這份安裝沒有 git,看不出現在跑的版本")
    assert h.engine.check_live(run) == 0 and h.engine.feature_requests()
    r = client.get("/api/requests").json()
    assert r["live_check_off"] == "這份安裝沒有 git,看不出現在跑的版本" and [x["text"] for x in r["items"]] == ["想要每週的花園澆水摘要"]


def test_requests_kept_before_these_fields_read_as_they_were_and_find_their_conversation_and_commit():
    h = make_harness(at(2026, 10, 26, 10))
    h.store.set_state(KEY, [
        {"ts": "2026-10-24T21:00:00.000100+08:00", "text": "想要每週的花園澆水摘要", "done": False},
        {"ts": "2026-10-25T16:00:00.000200+08:00", "text": "深談裡的草圖要能直接看", "done": False},
    ])
    assert [r["text"] for r in h.engine.feature_requests()] == ["想要每週的花園澆水摘要", "深談裡的草圖要能直接看"]
    h.store.save_thread("t1", {"about": None, "ts": "2026-10-24T21:00", "turns": [
        {"role": "user", "text": "記一下", "ts": "2026-10-24T21:00:00+08:00"},
        {"role": "muninn", "text": "記到需求清單了(目前 1 項),下次會處理", "ts": "2026-10-24T21:00:00.000500+08:00"}]})
    h.store.save_thread("t2", {"about": None, "ts": "2026-10-25T15:00", "turns": []})
    h.store.add_lease({"lease_id": "L1", "thread": "t2", "project": "garden", "mode": "chat", "state": "active",
                       "started": "2026-10-25T15:00:00+08:00"})
    h.store.add_lease_turn("L1", "muninn", "記到需求清單了(目前 2 項),下次會處理", at(2026, 10, 25, 16))
    h.engine.requests = Requests(h.store, h.clock, h.engine.gate, FakeVersion("feed003"),
                                 speak=lambda tid, text, via, buttons: h.engine.conversation().speak(None, text, buttons, thread=tid, via=via), words=ZH)
    assert h.engine.check_live(run) == 0  # the old ones get their fields; none has a commit yet
    assert h.engine.request_commit(2, "feed003") and not h.engine.request_commit(9, "feed003")
    assert h.engine.check_live(run) == 1
    first, second = h.store.get_state(KEY)
    assert (first["id"], first["thread"], first["via"], first["commit"], first["done"]) == (1, "t1", [], None, False)
    assert (second["id"], second["thread"], second["via"], second["commit"], second["done"]) == (2, "t2", ["lease:L1"], "feed003", True)
    said = turns(h, "t2")[-1]
    assert said["text"] == "上線了:「深談裡的草圖要能直接看」" and said["local_only"] and said["via"] == ["lease:L1"]  # kept to that talk
    h.engine.undo_action([a for a in h.engine.undoable_actions() if "已上線" in a.text][0].id)
    assert h.engine.check_live(run) == 0 and h.store.get_state(KEY)[1]["commit"] == "feed003"  # the step does not run again


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def test_the_git_version_reads_the_clone_it_runs_from_and_the_deploy_log(tmp_path):
    repo = tmp_path / "muninn"
    repo.mkdir()
    _git(repo, "init", "-q")
    for n in (1, 2):
        (repo / "f").write_text(str(n))
        _git(repo, "add", "f")
        _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", f"c{n}")
    first, second = _git(repo, "rev-parse", "HEAD~1"), _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", first)
    log = tmp_path / "data" / "deploys.log"
    v = GitVersion.at(repo, log)
    assert v is not None and v.unknown is None and v.head() == first
    assert v.contains(first[:7]) is True and v.contains(second) is False
    assert v.contains("0000000") is None and v.contains("--all") is None  # not a commit this history knows
    assert v.deploy_lines() == []
    log.parent.mkdir()
    log.write_text("2026-10-25 10:00 aaa0001 -> bbb0002\n\n2026-10-25 11:00 rollback bbb0002 -> aaa0001: 首頁打不開\n")
    assert v.deploy_lines() == ["2026-10-25 10:00 aaa0001 -> bbb0002", "2026-10-25 11:00 rollback bbb0002 -> aaa0001: 首頁打不開"]
    assert GitVersion.at(tmp_path / "nowhere", log) is None


def test_the_server_looks_when_it_starts(tmp_path):
    import time

    from starlette.testclient import TestClient

    from muninn.adapters.channels.webpush import VapidKeys
    from muninn.adapters.web.app import create_app
    from muninn.adapters.web.devices import DeviceRegistry

    h = make_harness(at(2026, 10, 25, 10))
    asked(h, "想要每週的花園澆水摘要")
    reqs = h.store.get_state(KEY)
    reqs[0]["commit"] = "c0ffee1"
    h.store.set_state(KEY, reqs)
    h.engine.requests.version = FakeVersion("c0ffee1")
    keys = VapidKeys(tmp_path / "secrets" / "vapid.pem")
    keys.ensure()
    with TestClient(create_app(h.engine, keys, "tok", DeviceRegistry(tmp_path / "dev.db")), base_url="https://testserver"):
        for _ in range(100):
            if not h.engine.feature_requests():
                break
            time.sleep(0.02)
    assert not h.engine.feature_requests() and turns(h, "t1")[-1]["text"].startswith("上線了")


def test_the_cli_keeps_the_commit_that_implements_a_request(tmp_path, capsys):
    from muninn import main
    from muninn.adapters.store_sqlite import SqliteStore

    SqliteStore(tmp_path / "muninn.db").set_state(KEY, [
        {"ts": "2026-10-24T21:00:00+08:00", "text": "想要每週的花園澆水摘要", "done": False, "id": 1}])
    assert main.main(["--data", str(tmp_path), "requests", "--commit", "1", "feed003"]) == 0
    assert main.main(["--data", str(tmp_path), "requests", "--commit", "7", "feed003"]) == 1
    assert SqliteStore(tmp_path / "muninn.db").get_state(KEY)[0]["commit"] == "feed003"
    assert main.main(["--data", str(tmp_path), "requests"]) == 0
    assert "(commit feed003)" in capsys.readouterr().out
