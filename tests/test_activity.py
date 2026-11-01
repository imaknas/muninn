"""Computer activity from any collector (contracts/activity.v1.md): what keeps to the contract is kept, as the core
reads it, and what does not is refused with why while the rest of the post is kept."""

from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from muninn.core.activity import Refused, observation, observations

WINDOW = {"source": "aw", "key": "e1", "ts": "2026-11-03T09:00:00+08:00", "kind": "window", "sensitivity": "personal",
          "payload": {"app": "Code", "title": "garden/plan.md", "duration_s": 300, "browser_profile": "work", "battery": 80}}
COMMIT = {"source": "git", "key": "garden:0a1b2c3d4e5f", "ts": "2026-11-03T09:10:00+08:00", "kind": "agent_commit",
          "payload": {"repo": "garden", "files": 2, "added": 10, "deleted": 1,
                      "exts": {".py": {"files": 2, "added": 10, "deleted": 1}}, "langs": ["Python"], "machine": "laptop"}}


def test_a_window_and_a_commit_are_kept_with_only_the_contracts_fields():
    w = observation(WINDOW)
    assert (w.key, w.kind, w.sensitivity.name) == ("aw:e1", "window", "PERSONAL")
    assert w.payload == {"app": "Code", "title": "garden/plan.md", "duration_s": 300, "browser_profile": "work"}  # no battery
    c = observation(COMMIT)
    assert c.key == "git:garden:0a1b2c3d4e5f" and c.payload["langs"] == ["Python"] and c.payload["machine"] == "laptop"
    assert observation({**WINDOW, "sensitivity": None}).sensitivity.name == "SENSITIVE"  # unsaid: the strictest


@pytest.mark.parametrize("change, says", [
    ({"kind": "keystrokes"}, "kind"),
    ({"ts": "2026-11-03T09:00:00"}, "time zone"),
    ({"source": "My Agent"}, "source"),
    ({"payload": {"app": "Code", "duration_s": -1}}, "duration_s"),
    ({"payload": {"app": "Code", "duration_s": 5, "browser_profile": "school"}}, "browser_profile"),
    ({"sensitivity": "secret"}, "sensitivity"),
])
def test_a_window_off_the_contract_is_refused_with_why(change, says):
    with pytest.raises(Refused, match=says):
        observation({**WINDOW, **change})


@pytest.mark.parametrize("change, says", [
    ({"source": "aw"}, '"git"'),
    ({"key": "garden-0a1b2c3d"}, "key"),  # gitleaks:allow (an invented, malformed key)
    ({"key": "garden:not-a-sha"}, "key"),
    ({"payload": {**COMMIT["payload"], "added": "ten"}}, "added"),
])
def test_a_commit_is_keyed_as_muninns_own_git_source_keys_it(change, says):
    with pytest.raises(Refused, match=says):
        observation({**COMMIT, **change})


def test_a_post_keeps_what_keeps_to_the_contract_and_says_what_did_not():
    kept, refused = observations([WINDOW, {**WINDOW, "kind": "keystrokes"}, COMMIT])
    assert [o.kind for o in kept] == ["window", "agent_commit"] and refused == [{"index": 1, "why": refused[0]["why"]}]
    assert "kind" in refused[0]["why"]


def test_another_collector_on_another_system_is_one_more_sender_of_the_same():
    """An invented Linux collector: its own name, the same two kinds; the core reads it like the Mac's."""
    from conftest import at, make_harness

    h = make_harness(at(2026, 11, 3, 10))
    kept, refused = observations([
        {"source": "linux-aw", "key": "w-1", "ts": "2026-11-03T09:00:00+08:00", "kind": "window",
         "payload": {"app": "firefox", "title": "Garden planner", "duration_s": 120}},
        {**COMMIT, "payload": {**COMMIT["payload"], "machine": "linux-desktop"}}])
    assert refused == [] and len(h.engine.ingest(kept)) == 2
    assert {o.source for o in h.store.observations(at(2026, 11, 3, 0))} >= {"linux-aw", "git"}


def test_a_computer_installs_its_collector_with_one_line_and_the_token_it_is_asked_for(web):
    h, client, devices = web
    assert client.get("/api/setup/activity").status_code == 401  # the token only to a paired device
    client.post("/api/pair", json={"code": devices.new_pairing_code(), "name": "phone"})
    s = client.get("/api/setup/activity").json()
    [mac] = s["collectors"]
    assert mac["command"] == "curl -fsSL https://testserver/collectors/mac/install.sh | bash" and s["token"] == "tok"
    other = TestClient(client.app, base_url="https://testserver")  # the Mac, not paired: the files are public code
    script = other.get("/collectors/mac/install.sh").text
    assert 'MUNINN_URL="${MUNINN_URL:-https://testserver}"' in script and "ssh" not in script
    assert re.search(r"\btok\b", script) is None  # the token is pasted on the computer, never served
    assert "def main()" in other.get("/collectors/mac/muninn_agent.py").text
    assert other.get("/collectors/mac/config.json").status_code == 404
    assert other.get("/collectors/../config.yaml").status_code == 404


def test_what_the_mac_collector_sends_keeps_to_the_contract():
    import importlib.util
    from datetime import datetime, timezone
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("agent", Path(__file__).parents[1] / "src/muninn/collectors/mac/muninn_agent.py")
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)
    windows = agent.window_observations([{"timestamp": "2026-11-03T01:00:00+00:00", "duration": 120,
                                          "data": {"app": "Google Chrome", "title": "Garden planner"}}], [], {"Google Chrome"})
    kept, refused = observations([*windows, {**COMMIT, "source": "git"}])
    assert refused == [] and kept[0].payload["browser_profile"] == "personal"
    assert datetime.now(timezone.utc)  # the module loaded as the Mac runs it
