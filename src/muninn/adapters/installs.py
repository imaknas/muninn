"""Where a release comes from, how this Muninn was installed and how it runs in the background: the pieces
`muninn upgrade` (upgrade.py) is given, one implementation each. The installation also adds an optional part
(an extra, such as the browser that fills application forms) and keeps the ones there through an upgrade."""

from __future__ import annotations

import importlib.util
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import httpx

from muninn.adapters.programs import USUAL, find_program
from muninn.upgrade import PACKAGE, Release, UpgradeError, stable

TIMEOUT_S = 15
INSTALL_TIMEOUT_S = 600


# ── where a release comes from ────────────────────────────────────────────


class PyPI:
    """The latest stable release on the Python Package Index: not a pre-release, and not yanked."""

    def __init__(self, get: Callable[[str], Any] | None = None, package: str = PACKAGE):
        self._get = get or (lambda url: httpx.get(url, timeout=TIMEOUT_S, follow_redirects=True))
        self._package = package

    def latest(self) -> Release | None:
        try:
            r = self._get(f"https://pypi.org/pypi/{self._package}/json")
        except httpx.HTTPError as e:
            raise UpgradeError(f"PyPI did not answer ({e})") from None
        if r.status_code == 404:
            return None  # not published yet
        if r.status_code != 200:
            raise UpgradeError(f"PyPI answered {r.status_code}")
        releases = (r.json() or {}).get("releases") or {}
        live = [v for v, files in releases.items() if files and stable(v) and not all(f.get("yanked") for f in files)]
        if not live:
            return None
        from packaging.version import Version

        best = max(live, key=Version)
        return Release(best, f"{self._package}=={best}")


class WheelFile:
    """A release named by hand (`muninn upgrade --from <wheel>`): a package file's path or address."""

    NAME = re.compile(r"muninn_ai-(?P<version>[^-]+)-")

    def __init__(self, target: str):
        self._target = target

    def latest(self) -> Release | None:
        m = self.NAME.search(Path(self._target.split("?")[0]).name)
        if not m:
            raise UpgradeError(f"{self._target} is not a muninn-ai package file (muninn_ai-<version>-…whl)")
        return Release(m["version"], self._target)


# ── how this Muninn was installed ─────────────────────────────────────────


def with_extras(spec: str, extras: tuple[str, ...]) -> str:
    """A release's spec asking for the extras too: muninn-ai==1.2 → muninn-ai[autofill]==1.2; a package file's path
    or address → muninn-ai[autofill] @ <its address>."""
    if not extras:
        return spec
    named = f"{PACKAGE}[{','.join(extras)}]"
    if spec.startswith(PACKAGE):
        return named + spec[len(PACKAGE):]
    return f"{named} @ {spec if '://' in spec else Path(spec).resolve().as_uri()}"


class UvTool:
    """Installed by the installer (or `uv tool install muninn-ai`): it replaces itself, keeping the extras it has."""

    kind = "uv tool"

    def __init__(self, uv: str, python: str, extras: tuple[str, ...] = (), executable: str = sys.executable,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        """`extras`: the optional parts installed now; `executable`: this installation's Python."""
        self._uv, self._python, self._extras, self._executable, self._run = uv, python, extras, executable, run

    def refusal(self) -> str | None:
        return None

    def extra_refusal(self) -> str | None:
        return None

    def install(self, spec: str) -> None:
        # --refresh-package: uv keeps the index's list of releases for a while, so a release minutes old is otherwise
        # "unsatisfiable" though PyPI already says it is out
        self._call([self._uv, "tool", "install", "--force", "--refresh-package", PACKAGE, "--python", self._python,
                    with_extras(spec, self._extras)], "uv")

    def uninstall(self) -> str:
        self._call([self._uv, "tool", "uninstall", PACKAGE], "uv")
        return f"{PACKAGE} uninstalled"

    def add_extra(self, extra: str, version: str, then: list[str] | None = None) -> None:
        """This version again with `extra` too; `then`: what to run with this installation's Python after (the
        browser the extra drives, say)."""
        self._extras = tuple(dict.fromkeys((*self._extras, extra)))
        self._call([self._uv, "tool", "install", "--force", "--python", self._python, with_extras(f"{PACKAGE}=={version}", self._extras)], "uv")
        if then:
            self._call([self._executable, *then], then[-1])

    def _call(self, argv: list[str], what: str) -> None:
        try:
            done = self._run(argv, capture_output=True, text=True, timeout=INSTALL_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as e:
            raise UpgradeError(f"{what} could not run ({e})") from None
        if done.returncode != 0:
            said = [line.strip() for line in (done.stderr or done.stdout or f"{what} failed").splitlines() if line.strip()]
            raise UpgradeError(" ".join(said)[-400:])


class Refused:
    """An install that does not replace itself: what upgrades it instead (`why`), and what adds an optional part to
    it instead (`extra_why`)."""

    def __init__(self, kind: str, why: str, extra_why: str | None = None, remove_why: str | None = None):
        self.kind, self._why, self._extra_why, self._remove_why = kind, why, extra_why or why, remove_why or why

    def refusal(self) -> str | None:
        return self._why

    def extra_refusal(self) -> str | None:
        return self._extra_why

    def install(self, spec: str) -> None:
        raise UpgradeError(self._why)

    def add_extra(self, extra: str, version: str, then: list[str] | None = None) -> None:
        raise UpgradeError(self._extra_why)

    def uninstall(self) -> str:
        raise UpgradeError(self._remove_why)


EXTRAS = {"autofill": "playwright"}  # each optional part, by a module only it brings


def installation(prefix: Path = Path(sys.prefix), package_dir: Path = Path(__file__).resolve().parents[1],
                 env: dict[str, str] | None = None, which: Callable[[str], str | None] = lambda name: find_program(name, (*USUAL, "~/.cargo/bin"))) -> Any:
    """How the running Muninn was installed, read from where it runs."""
    env = dict(os.environ) if env is None else env
    if env.get("MUNINN_CONTAINER"):
        return Refused("container", "this Muninn runs in a container: upgrade it where its repository is cloned, with "
                                    "git pull && docker compose up -d --build",
                       "this Muninn runs in a container, which has no browser to fill forms in: for that, install Muninn on "
                       "a computer with the one-line installer",
                       "this Muninn runs in a container: remove it with docker compose down (-v deletes its data too)")
    root = package_dir.parents[1]  # <checkout>/src/muninn
    if (root / ".git").exists() and (root / "pyproject.toml").is_file():
        return Refused("checkout", f"this Muninn runs from a source checkout ({root}): install the released package "
                                   f"instead (uv tool install {PACKAGE}), or check out the release's tag there",
                       f"this Muninn runs from a source checkout ({root}): run uv sync --extra autofill and "
                       f"uv run playwright install chromium there",
                       f"this Muninn runs from a source checkout ({root}): delete that folder to remove it")
    if (prefix / "uv-receipt.toml").is_file():
        uv = which("uv")
        if uv is None:
            return Refused("uv tool", "uv, which installed this Muninn, is not found: install uv, then run muninn upgrade again",
                           "uv, which installed this Muninn, is not found: install uv, then try again")
        extras = tuple(x for x, module in EXTRAS.items() if importlib.util.find_spec(module) is not None)
        return UvTool(uv, f"{sys.version_info.major}.{sys.version_info.minor}", extras)
    return Refused("other", f"this Muninn was not installed by the installer: upgrade it the way it was installed "
                            f"(pip install --upgrade {PACKAGE})",
                   f"this Muninn was not installed by the installer: add form filling the way it was installed "
                   f"(pip install '{PACKAGE}[autofill]', then playwright install chromium)",
                   f"this Muninn was not installed by the installer: remove it the way it was installed (pip uninstall {PACKAGE})")


# ── how it runs in the background ─────────────────────────────────────────


class Launchd:
    def __init__(self, label: str, plist: Path, run: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self._label, self._plist, self._run = label, plist, run

    def restart(self) -> bool:
        done = self._run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{self._label}"], capture_output=True, timeout=TIMEOUT_S)
        if done.returncode != 0:
            raise UpgradeError(f"launchd did not restart {self._label}")
        return True

    def remove(self) -> str:
        """Stopped, and not started at login again."""
        self._run(["launchctl", "bootout", f"gui/{os.getuid()}/{self._label}"], capture_output=True, timeout=TIMEOUT_S)
        self._plist.unlink(missing_ok=True)
        return f"launchd: {self._label} stopped and removed"


class SystemdUser:
    def __init__(self, unit: str, path: Path, run: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self._unit, self._path, self._run = unit, path, run

    def restart(self) -> bool:
        done = self._run(["systemctl", "--user", "restart", self._unit], capture_output=True, timeout=TIMEOUT_S)
        if done.returncode != 0:
            raise UpgradeError(f"systemd did not restart {self._unit}")
        return True

    def remove(self) -> str:
        self._run(["systemctl", "--user", "disable", "--now", self._unit], capture_output=True, timeout=TIMEOUT_S)
        self._path.unlink(missing_ok=True)
        self._run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=TIMEOUT_S)
        return (f"systemd: {self._unit} stopped and removed (left as it is: loginctl enable-linger, which other services "
                f"of yours may need; loginctl disable-linger undoes it)")


class RunLoop:
    """Where there was no service manager, install.sh keeps Muninn running with a loop of its own (run.sh), which
    starts it again whenever it stops: it says its own process (run.pid) and Muninn's (run.child)."""

    def __init__(self, data_dir: Path, kill: Callable[[int, int], None] = os.kill):
        self._dir, self._kill = data_dir, kill

    def _pid(self, name: str) -> int | None:
        try:
            return int((self._dir / name).read_text().strip())
        except (OSError, ValueError):
            return None

    def restart(self) -> bool:
        child = self._pid("run.child")
        if child is None:
            return False  # an older run.sh, which does not say: the user restarts it
        try:
            self._kill(child, signal.SIGTERM)  # the loop starts it again, on what is installed now
        except OSError:
            pass
        return True

    def remove(self) -> str:
        loop = self._pid("run.pid")
        if loop is not None:
            try:
                self._kill(loop, signal.SIGTERM)  # the loop stops Muninn as it ends
            except OSError:
                pass
        for name in ("run.sh", "run.pid", "run.child"):
            (self._dir / name).unlink(missing_ok=True)
        return "the background loop stopped and removed"


class NoService:
    def restart(self) -> bool:
        return False

    def remove(self) -> str:
        return "no background service was set up"


def service(home: Path = Path.home(), platform: str = sys.platform, data_dir: Path | None = None) -> Any:
    """The background service the installer made (launchd on macOS, systemd --user on Linux, or its own loop where
    there is neither), if there is one."""
    plist = home / "Library/LaunchAgents/app.muninn.plist"
    if platform == "darwin" and plist.is_file():
        return Launchd("app.muninn", plist)
    unit = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "systemd/user/muninn.service"
    if platform.startswith("linux") and unit.is_file():
        return SystemdUser("muninn.service", unit)
    if data_dir is not None and (data_dir / "run.pid").is_file():
        return RunLoop(data_dir)
    return NoService()


def served_version(port: int, get: Callable[[str], Any] | None = None) -> Callable[[], str | None]:
    """The version the local server answers with, or None while it does not answer."""
    get = get or (lambda url: httpx.get(url, timeout=3))

    def served() -> str | None:
        try:
            r = get(f"http://127.0.0.1:{port}/healthz")
            return (r.json() or {}).get("version") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
    return served
