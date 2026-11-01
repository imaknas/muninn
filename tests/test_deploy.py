"""bin/deploy.sh against a scratch world: a bare repo as the remote, a checkout
on main, and a runtime clone. npm, uv and curl are stubs on PATH, and the
restart and smoke steps go through the script's test overrides, so nothing
builds, nothing touches launchd and nothing leaves tmp_path.

    uv run pytest -m deploy
"""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = pytest.mark.deploy

SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "deploy.sh"

# The uv stub stands in for the test suite, `uv sync` (which provides the
# runtime's muninn entry point) and the browser tests. DEPLOY_TEST_ON_TESTS is
# a shell snippet run in the middle of the unit tests, in the checkout.
UV_STUB = """#!/bin/sh
if [ "$*" = "run pytest -q -n 4" ]; then
  echo "tests-start $(pwd)" >> "$DEPLOY_TEST_EVENTS"
  if [ -n "${DEPLOY_TEST_ON_TESTS:-}" ]; then sh -c "$DEPLOY_TEST_ON_TESTS"; fi
  echo "tests-end $(pwd)" >> "$DEPLOY_TEST_EVENTS"
elif [ "$1" = "sync" ]; then
  mkdir -p .venv/bin
  printf '#!/bin/sh\\nexit 0\\n' > .venv/bin/muninn
  chmod +x .venv/bin/muninn
fi
exit 0
"""
STUBS = {
    "uv": UV_STUB,
    "npm": "#!/bin/sh\nexit 0\n",
    # The server is healthy unless the version it runs carries an UNHEALTHY file.
    "curl": '#!/bin/sh\ntest ! -e "$MUNINN_RUNTIME/UNHEALTHY"\n',
    "launchctl": '#!/bin/sh\necho launchctl >> "$DEPLOY_TEST_EVENTS"\nexit 99\n',
}
RESTART = 'echo "restart $(git -C "$MUNINN_RUNTIME" rev-parse --short HEAD)" >> "$DEPLOY_TEST_EVENTS"'
# The smoke check fails on a version that carries a BROKEN file, and notes what
# the remote had at that moment.
SMOKE = (
    'echo "smoke origin=$(git -C "$DEPLOY_TEST_ORIGIN" rev-parse --short main)" >> "$DEPLOY_TEST_EVENTS";'
    ' test ! -e "$MUNINN_RUNTIME/BROKEN"'
)
STAMP = r"\d{4}-\d\d-\d\d \d\d:\d\d"


@dataclass
class World:
    origin: Path
    checkout: Path
    runtime: Path
    events: Path
    env: dict[str, str]

    def git(self, repo: Path, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(repo), *args], env=self.env, check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, repo: Path, message: str, add: str | None = None, remove: str | None = None) -> str:
        if add:
            (repo / add).write_text(message + "\n")
            self.git(repo, "add", add)
        if remove:
            self.git(repo, "rm", "-q", remove)
        self.git(repo, "commit", "-q", "--allow-empty", "-m", message)
        return self.git(repo, "rev-parse", "HEAD")

    def short(self, sha: str) -> str:
        return self.git(self.checkout, "rev-parse", "--short", sha)

    def head(self, repo: Path, ref: str = "HEAD") -> str:
        return self.git(repo, "rev-parse", ref)

    def start(self, checkout: Path | None = None, **env: str) -> subprocess.Popen:
        script = (checkout or self.checkout) / "bin" / "deploy.sh"
        return subprocess.Popen(
            ["bash", str(script)],
            env={**self.env, **env},
            cwd=self.events.parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    def deploy(self, checkout: Path | None = None, **env: str) -> tuple[int, str]:
        p = self.start(checkout, **env)
        out, _ = p.communicate(timeout=60)
        return p.returncode, out

    def log(self) -> list[str]:
        return (self.runtime / "data" / "deploys.log").read_text().splitlines()

    def event_lines(self) -> list[str]:
        return self.events.read_text().splitlines() if self.events.exists() else []


@pytest.fixture
def world(tmp_path) -> World:
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = Deploy Test\n\temail = deploy@example.invalid\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in STUBS.items():
        (stubs / name).write_text(body)
        (stubs / name).chmod(0o755)
    origin, checkout, runtime = tmp_path / "origin.git", tmp_path / "checkout", tmp_path / "runtime"
    events = tmp_path / "events"
    env = {
        "PATH": f"{stubs}:{os.environ['PATH']}",
        "HOME": str(home),
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "MUNINN_RUNTIME": str(runtime),
        "MUNINN_LABEL": "test.muninn.deploy",
        "MUNINN_PORT": "18999",
        "MUNINN_RESTART_CMD": RESTART,
        "MUNINN_SMOKE_CMD": SMOKE,
        "MUNINN_HEALTH_WAIT": "2",
        "MUNINN_LOCK_WAIT": "30",
        "DEPLOY_TEST_EVENTS": str(events),
        "DEPLOY_TEST_ORIGIN": str(origin),
        "MUNINN_TESTS": "here",  # the suites stubbed here, whatever runner this machine has
    }
    w = World(origin, checkout, runtime, events, env)
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], env=env, check=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(checkout)], env=env, check=True)
    (checkout / "bin").mkdir()
    shutil.copy(SCRIPT, checkout / "bin" / "deploy.sh")
    (checkout / ".gitignore").write_text(".venv/\ndata/\n")
    w.git(checkout, "add", ".")
    w.commit(checkout, "base")
    w.git(checkout, "remote", "add", "origin", str(origin))
    w.git(checkout, "push", "-q", "-u", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(runtime)], env=env, check=True)
    return w


def test_good_deploy_goes_live_then_pushes(world):
    base = world.head(world.checkout)
    sha = world.commit(world.checkout, "feature", add="feature.txt")

    code, out = world.deploy()

    assert code == 0, out
    assert world.head(world.runtime) == sha
    assert world.head(world.origin, "main") == sha
    assert re.fullmatch(rf"{STAMP} {world.short(base)} -> {world.short(sha)}", world.log()[-1])
    # The smoke check ran while the remote still had the old version.
    assert f"smoke origin={world.short(base)}" in world.event_lines()
    assert "launchctl" not in world.event_lines()
    # The runtime still pulls from the real remote.
    assert world.git(world.runtime, "remote", "get-url", "origin") == str(world.origin)
    later = world.commit(world.checkout, "later", add="later.txt")
    world.git(world.checkout, "push", "-q", "origin", "main")
    world.git(world.runtime, "pull", "-q", "--ff-only", "origin", "main")
    assert world.head(world.runtime) == later


def test_failed_smoke_check_rolls_back_and_pushes_nothing(world):
    base = world.head(world.checkout)
    bad = world.commit(world.checkout, "broken", add="BROKEN")

    code, out = world.deploy()

    assert code == 5, out
    assert world.head(world.runtime) == base
    assert not (world.runtime / "BROKEN").exists()
    assert world.head(world.origin, "main") == base
    assert re.fullmatch(rf"{STAMP} rollback {world.short(bad)} -> {world.short(base)}: smoke check failed", world.log()[-1])
    restarts = [e for e in world.event_lines() if e.startswith("restart")]
    assert restarts == [f"restart {world.short(bad)}", f"restart {world.short(base)}"]
    # No tests on the way back: they ran once, for the new version.
    assert [e for e in world.event_lines() if e.startswith("tests-start")] == [f"tests-start {world.checkout}"]

    # The rolled-back runtime takes the fix like any other deploy.
    fix = world.commit(world.checkout, "fix", remove="BROKEN")
    code, out = world.deploy()
    assert code == 0, out
    assert world.head(world.runtime) == fix
    assert world.head(world.origin, "main") == fix


def test_server_never_healthy_rolls_back(world):
    base = world.head(world.checkout)
    bad = world.commit(world.checkout, "unhealthy", add="UNHEALTHY")

    code, out = world.deploy()

    assert code == 3, out
    assert world.head(world.runtime) == base
    assert world.head(world.origin, "main") == base
    assert re.fullmatch(
        rf"{STAMP} rollback {world.short(bad)} -> {world.short(base)}: server not healthy on port 18999", world.log()[-1]
    )
    assert not any(e.startswith("smoke") for e in world.event_lines())


def test_failed_rollback_stops_with_the_manual_steps(world):
    base = world.head(world.checkout)
    world.commit(world.checkout, "broken", add="BROKEN")

    # Restarting works for the broken version and fails for the one before it.
    code, out = world.deploy(MUNINN_RESTART_CMD='test -e "$MUNINN_RUNTIME/BROKEN"')

    assert code == 7, out
    assert "ROLLBACK STUCK at: restart" in out
    assert f"git reset --hard {world.short(base)}" in out
    assert world.head(world.origin, "main") == base
    assert not (world.runtime / "data" / "deploys.log").exists()


def test_second_deploy_waits_for_the_first(world, tmp_path):
    base = world.head(world.checkout)
    first = world.commit(world.checkout, "first", add="first.txt")
    # Another checkout of the same project, deploying a commit on top.
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(world.checkout), str(other)], env=world.env, check=True)
    world.git(other, "remote", "set-url", "origin", str(world.origin))
    second = world.commit(other, "second", add="second.txt")

    a = world.start(DEPLOY_TEST_ON_TESTS="sleep 2")
    deadline = time.monotonic() + 20
    while f"tests-start {world.checkout}" not in world.event_lines():
        assert time.monotonic() < deadline and a.poll() is None, a.communicate()[0]
        time.sleep(0.05)
    b = world.start(other)
    out_a, _ = a.communicate(timeout=60)
    out_b, _ = b.communicate(timeout=60)

    assert a.returncode == 0, out_a
    assert b.returncode == 0, out_b
    assert "waiting for another deploy" in out_b
    tests = [e for e in world.event_lines() if e.startswith("tests-")]
    assert tests == [
        f"tests-start {world.checkout}",
        f"tests-end {world.checkout}",
        f"tests-start {other}",
        f"tests-end {other}",
    ]
    assert world.head(world.runtime) == second
    assert world.head(world.origin, "main") == second
    assert [line[17:] for line in world.log()] == [
        f"{world.short(base)} -> {world.short(first)}",
        f"{world.short(first)} -> {world.short(second)}",
    ]


def test_deploy_gives_up_when_the_lock_stays_taken(world):
    before = world.head(world.runtime)
    world.commit(world.checkout, "feature", add="feature.txt")
    (world.runtime / "data").mkdir()
    with open(world.runtime / "data" / "deploy.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        code, out = world.deploy(MUNINN_LOCK_WAIT="1")

    assert code == 6, out
    assert "someone else is deploying" in out
    assert world.head(world.runtime) == before
    assert world.event_lines() == []


def test_main_moving_during_the_tests_stops_the_deploy(world):
    before = world.head(world.runtime)
    world.commit(world.checkout, "pinned", add="pinned.txt")

    # Something lands on main while the tests run: they ran on a mix, so nothing goes live.
    code, out = world.deploy(DEPLOY_TEST_ON_TESTS="echo moved > moved.txt && git add moved.txt && git commit -q -m moved")

    assert code == 1 and "moved" in out, out
    assert world.head(world.runtime) == before
    assert world.head(world.origin, "main") == before


def test_main_moving_after_the_tests_does_not_go_live(world):
    pinned = world.commit(world.checkout, "pinned", add="pinned.txt")

    # Something lands on main while the runtime restarts: the pinned commit is what goes live and is pushed.
    lands = f'cd "{world.checkout}" && echo moved > moved.txt && git add moved.txt && git commit -q -m moved'
    code, out = world.deploy(MUNINN_RESTART_CMD=f"{RESTART}; {lands}")

    assert code == 0, out
    assert world.head(world.checkout) != pinned
    assert world.head(world.runtime) == pinned and not (world.runtime / "moved.txt").exists()
    assert world.head(world.origin, "main") == pinned



def test_with_the_platforms_test_runner_the_pinned_commit_is_handed_to_it(world, tmp_path):
    stub = tmp_path / "stubs" / "agenthub"
    stub.write_text('#!/bin/sh\necho "agenthub $*" >> "$DEPLOY_TEST_EVENTS"\n')
    stub.chmod(0o755)
    sha = world.commit(world.checkout, "feat: something new")
    code, out = world.deploy(MUNINN_TESTS="")  # unset: the runner is installed, so it is used
    assert code == 0, out
    assert f"agenthub test --when deploy --rev {sha}" in world.event_lines()
    assert not [x for x in world.event_lines() if x.startswith("tests-start")]  # not run here as well
    stub.write_text("#!/bin/sh\nexit 1\n")  # a tier fails
    world.commit(world.checkout, "feat: something broken")
    code, out = world.deploy(MUNINN_TESTS="platform")
    assert code == 4 and "tests failed" in out
