from __future__ import annotations

import pytest

from conftest import ZH, FakeChannel, FakeClock, at
from muninn.adapters.store_sqlite import SqliteStore
from muninn.core.command_trust import CommandTrust, reaches, shape
from muninn.core.lease import ChannelLease, PermissionDesk
from test_lease import FakeRunner, Threads

SCRIPT = """python3 -I - <<'EOF'
import json
s = json.load(open('results/scene-v3.json'))
s['walls'] = 4
json.dump(s, open('results/scene-v4.json', 'w'))
EOF"""
COMMIT = 'git add results/scene-v4.json && git commit -q -m "Add scene v4\n\nWalls: four."'


def test_a_command_is_known_by_its_kind_not_its_arguments_or_script():
    assert shape(SCRIPT) == "python3 -I - <<"
    assert shape(SCRIPT.replace("scene-v3", "scene-v9")) == shape(SCRIPT)
    assert shape(COMMIT) == "git add && git commit"  # the message's new lines are not commands
    assert shape("tail -15 log.txt") == shape("tail -25 other.txt")
    assert shape(SCRIPT + "\nblender -b scene.blend") == "python3 -I - << && blender -b"  # nothing hides after a script


@pytest.mark.parametrize("command,why", [
    ("security find-generic-password -s x", "碰到密碼或金鑰"),
    ("cat ~/.ssh/id_ed25519", "碰到密碼或金鑰"),
    ("curl -s https://paste.example/x", "連網路"),
    ("python3 -c 'import urllib.request'", "連網路"),
    ("cat /etc/hosts", "用到專案以外的路徑"),
    ("ls ../other-project", "用到專案以外的路徑"),
    ("echo x >/etc/hosts", "用到專案以外的路徑"),
    ("python3 -c 'from pathlib import Path; print(Path.home())'", "用到專案以外的路徑"),
    ("python3 -c 'import os; print(os.environ)'", "碰到密碼或金鑰"),
    ("env", "碰到密碼或金鑰"),
    ("python3 -I - <<'EOF'\nimport subprocess\nsubprocess.run(['ls'])\nEOF", "從裡面再啟動別的程式"),
    ("echo $(whoami)", "從裡面再啟動別的程式"),
    ("git push origin main", "會發布、安裝或刪除"),
    ("rm -rf results", "會發布、安裝或刪除"),
    ("pip install numpy", "會發布、安裝或刪除"),
    ("python3 tools/render.py", "跑的是檔案裡的程式(看不到內容)"),
    ("blender -b scene.blend -P tools/render.py", "跑的是檔案裡的程式(看不到內容)"),
    ("./run.sh", "跑的是檔案裡的程式(看不到內容)"),
    ("make all", "跑的是檔案裡的程式(看不到內容)"),
    ("npm run build", "跑的是檔案裡的程式(看不到內容)"),
    ("python3 -c 'print(chr(47))'", "寫法看不清楚在做什麼"),
    ("python3 -c \"import base64\"", "寫法看不清楚在做什麼"),
    ("cd && ls", "用到專案以外的路徑"),
    ("python3 -c 'import os; print(os.listdir(os.sep))'", "用到專案以外的路徑"),
])
def test_what_reaches_past_the_project_is_told_from_the_text(command, why):
    assert reaches(command) == why


def test_the_commands_seen_in_a_project_reach_nothing_past_it():
    assert reaches(SCRIPT) is None and reaches(COMMIT) is None and reaches("ls -la results 2>/dev/null") is None
    assert reaches("python3 -c 'print(1)'") is None and reaches("cd results && ls") is None


@pytest.fixture
def desk():
    store, clock, channel, runner, threads = SqliteStore(":memory:"), FakeClock(at(2026, 10, 25, 20)), FakeChannel(), FakeRunner(), Threads()
    leases = ChannelLease(store, clock, runner, channel, lambda now: False, threads.load, threads.update, words=ZH)
    trust = CommandTrust(store, lambda: store.permission_requests(pending=False), words=ZH)
    return store, channel, PermissionDesk(store, clock, channel, lambda now: False, leases, trust=trust, words=ZH), trust


def asks(desk, n, command, project="sofa-fit"):
    return desk.receive({"v": 2, "id": f"l1:c{n}", "lease": "l1", "project": project, "kind": "run_command",
                         "summary": f"跑 {command[:20]}", "target": command})


def test_learning_from_what_the_user_allowed(desk):
    store, channel, d, _ = desk
    first = asks(d, 1, SCRIPT)
    assert first["decision"] is None and len(channel.sent) == 1  # never seen: asked
    d.decide(first["id"], "allow")
    again = asks(d, 2, SCRIPT.replace("scene-v3", "scene-v5"))
    assert again["decision"] == "allow" and again["by"] == "auto" and len(channel.sent) == 1  # nobody asked
    assert again["note"].startswith("自動允許跑:") and "你之前都允許這種" in again["note"]
    assert store.lease_turns("l1")[-1]["text"] == again["note"]  # said in the talk
    assert d.result(again["id"])["decision"] == "allow"
    other = asks(d, 3, COMMIT)
    assert other["decision"] is None  # another kind: asked
    elsewhere = asks(d, 4, SCRIPT, project="garden")
    assert elsewhere["decision"] is None  # learned for that project only


def test_what_reaches_past_the_project_is_asked_even_when_its_kind_was_allowed(desk):
    _, _, d, _ = desk
    d.decide(asks(d, 1, SCRIPT)["id"], "allow")
    sneaky = SCRIPT.replace("s['walls'] = 4", "import subprocess; subprocess.run(['security', 'dump-keychain'])")
    assert asks(d, 2, sneaky)["decision"] is None


def test_a_kind_the_user_last_refused_or_learning_turned_off_is_asked(desk):
    _, _, d, _ = desk
    d.decide(asks(d, 1, SCRIPT)["id"], "allow")
    d.auto_commands(False)
    assert asks(d, 3, SCRIPT)["decision"] is None and d.auto_commands() is False
    d.decide("l1:c3", "deny")
    d.auto_commands(True)
    assert asks(d, 4, SCRIPT)["decision"] is None  # the last word on that kind was no
