"""`muninn upgrade` on a real install: two packages built from this tree, the first installed the way the installer
installs it (a uv tool, in a scratch tool folder), then upgraded to the second by the first's own command.

    uv run pytest -m deploy tests/test_upgrade_install.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.deploy

ROOT = Path(__file__).resolve().parents[1]


def package(version: str, out: Path, tmp: Path) -> Path:
    """This tree packaged as `version` (the web app is not built: the upgrade does not need it)."""
    src = tmp / f"src-{version}"
    shutil.copytree(ROOT / "src", src / "src", ignore=shutil.ignore_patterns("__pycache__", "node_modules", "*.js"))
    for name in ("README.md", "LICENSE"):
        shutil.copy(ROOT / name, src / name)
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    (src / "pyproject.toml").write_text(text.replace(f'version = "{_version(text)}"', f'version = "{version}"', 1), encoding="utf-8")
    subprocess.run(["uv", "build", "-q", "--wheel", "-o", str(out)], cwd=src, check=True)
    return next(out.glob(f"muninn_ai-{version}-*.whl"))


def _version(pyproject: str) -> str:
    return next(line.split('"')[1] for line in pyproject.splitlines() if line.startswith("version = "))


def test_an_installed_muninn_upgrades_itself_to_a_newer_package(tmp_path):
    if shutil.which("uv") is None:
        pytest.skip("uv is not installed")
    old, new = package("90.0.1", tmp_path / "dist", tmp_path), package("90.0.2", tmp_path / "dist", tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text("port: 18897\n")
    env = {**os.environ, "UV_TOOL_DIR": str(tmp_path / "tools"), "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
           "MUNINN_DATA": str(data), "HOME": str(tmp_path)}  # no launchd or systemd unit here: nothing is restarted
    env.pop("MUNINN_CONTAINER", None)
    subprocess.run(["uv", "tool", "install", "-q", "--python", "3.13", str(old)], env=env, check=True)
    muninn = str(tmp_path / "bin" / "muninn")
    assert subprocess.run([muninn, "--version"], env=env, capture_output=True, text=True).stdout.strip() == "muninn 90.0.1"
    done = subprocess.run([muninn, "upgrade", "--from", str(new)], env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout.splitlines() == ["90.0.1 is installed; 90.0.2 is available", "installing 90.0.2",
                                        "90.0.2 is installed; restart Muninn (muninn serve) to run it"]
    assert subprocess.run([muninn, "--version"], env=env, capture_output=True, text=True).stdout.strip() == "muninn 90.0.2"
    again = subprocess.run([muninn, "upgrade", "--from", str(new)], env=env, capture_output=True, text=True)
    assert (again.returncode, again.stdout.strip()) == (0, "90.0.2 is the latest stable release")


def test_each_background_service_is_stopped_and_removed(tmp_path):
    import signal
    from types import SimpleNamespace

    from muninn.adapters import installs

    ran = []
    run = lambda argv, **kw: ran.append(argv) or SimpleNamespace(returncode=0)  # noqa: E731
    plist = tmp_path / "app.muninn.plist"
    plist.write_text("<plist/>")
    installs.Launchd("app.muninn", plist, run=run).remove()
    assert ran[-1][:2] == ["launchctl", "bootout"] and not plist.exists()
    unit = tmp_path / "muninn.service"
    unit.write_text("[Unit]")
    installs.SystemdUser("muninn.service", unit, run=run).remove()
    assert ["systemctl", "--user", "disable", "--now", "muninn.service"] in ran and not unit.exists()

    killed = []
    for name, pid in (("run.pid", "100"), ("run.child", "101"), ("run.sh", "#!/bin/sh")):
        (tmp_path / name).write_text(pid)
    assert isinstance(installs.service(home=tmp_path / "home", platform="linux", data_dir=tmp_path), installs.RunLoop)
    loop = installs.RunLoop(tmp_path, kill=lambda pid, sig: killed.append((pid, sig)))
    assert loop.restart() is True and killed == [(101, signal.SIGTERM)]  # Muninn stopped: the loop starts the new one
    loop.remove()
    assert killed[-1] == (100, signal.SIGTERM) and not any((tmp_path / n).exists() for n in ("run.pid", "run.child", "run.sh"))


def test_uninstall_keeps_the_data_unless_asked_and_says_what_it_did(tmp_path, monkeypatch):
    import argparse

    from muninn import main
    from muninn.adapters import installs

    data = tmp_path / "data"
    data.mkdir()
    (data / "config.yaml").write_text("jobs: default\n")
    removed = []

    class Tool:
        def uninstall(self):
            removed.append("tool")
            return "muninn-ai uninstalled"

    monkeypatch.setattr(installs, "installation", lambda: Tool())
    monkeypatch.setattr(installs, "service", lambda **kw: installs.NoService())
    monkeypatch.setattr(main, "Tailscale", lambda: type("T", (), {"unserve": lambda self, port: []})())
    said = []
    assert main.uninstall(argparse.Namespace(data=str(data), delete_data=False), said.append) == 0
    assert data.is_dir() and removed == ["tool"] and any("kept" in s for s in said)
    assert main.uninstall(argparse.Namespace(data=str(data), delete_data=True), said.append) == 0
    assert not data.exists()
