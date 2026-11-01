"""Upgrading an installed Muninn to the latest stable release (`muninn upgrade`), so nobody has to pull and build.

The steps, with what varies between installs given (adapters/installs.py):
1. which release is the latest stable one (`ReleaseSource`: PyPI, or a wheel named by hand);
2. whether this install can replace itself (`Installation`: one made by the installer, a uv tool, can; a container or
   a git checkout cannot, and the user is told the one thing that upgrades it);
3. installing it, then handing over to the new code (`finish`: the old process's files are gone from under it);
4. the new code restarts the background service (`Service`) and waits until the server answers with the new version;
   when it does not, the version before is put back. The database is migrated by the new server itself when it starts
   (a copy is taken first, in data/backups).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Protocol

from packaging.version import InvalidVersion, Version

PACKAGE = "muninn-ai"


class UpgradeError(Exception):
    """A step that could not be done, said as the user is told it."""


@dataclass(frozen=True)
class Release:
    version: str
    spec: str  # what the installer is given: "muninn-ai==X", or a wheel's path or address


class ReleaseSource(Protocol):
    def latest(self) -> Release | None:
        """The latest stable release (None: there is none yet). UpgradeError when it cannot be told."""
        ...


class Installation(Protocol):
    kind: str

    def refusal(self) -> str | None:
        """Why this install cannot replace itself, with what does upgrade it; None when it can."""
        ...

    def install(self, spec: str) -> None:
        """Installs that release over this one. UpgradeError when it did not."""
        ...


class Service(Protocol):
    def restart(self) -> bool:
        """Restarts the background service; False when Muninn runs as none (the user restarts it)."""
        ...


def stable(version: str) -> bool:
    try:
        v = Version(version)
    except InvalidVersion:
        return False
    return not (v.is_prerelease or v.is_devrelease)


def newer(candidate: str, than: str) -> bool:
    return Version(candidate) > Version(than)


class Upgrader:
    def __init__(self, current: str, source: ReleaseSource, installation: Installation, service: Service,
                 served: Callable[[], str | None], say: Callable[[str], None], *, wait_s: float = 60,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        """`current`: the version running this command; `served()`: the version the local server answers with
        (None: it does not answer); `say`: tells the user one line."""
        self.current = current
        self._source, self._installation, self._service = source, installation, service
        self._served, self._say = served, say
        self._wait_s, self._sleep, self._clock = wait_s, sleep, clock

    def check(self) -> Release | None:
        """The release to upgrade to (None: this is the latest)."""
        latest = self._source.latest()
        if latest is None:
            self._say(f"no stable release of {PACKAGE} was found; {self.current} is installed")
            return None
        if not newer(latest.version, self.current):
            self._say(f"{self.current} is the latest stable release")
            return None
        self._say(f"{self.current} is installed; {latest.version} is available")
        return latest

    def run(self, handover: Callable[[str, str], int]) -> int:
        """Installs the latest stable release, then `handover(previous, target)` runs `finish` in the new code. The
        exit code: 0 upgraded or nothing to do, 1 nothing changed (no release, install failed), 2 this install does
        not upgrade itself, 3 the new version did not come up and the previous one is back (or could not be)."""
        if (why := self._installation.refusal()) is not None:
            self._say(why)
            return 2
        try:
            target = self.check()
        except UpgradeError as e:
            self._say(f"could not tell the latest release: {e}")
            return 1
        if target is None:
            return 0
        self._say(f"installing {target.version}")
        try:
            self._installation.install(target.spec)
        except UpgradeError as e:
            self._say(f"not upgraded, {self.current} stays: {e}")
            return 1
        return handover(self.current, target.version)

    def finish(self, previous: str, target: str) -> int:
        """In the new code: restart and wait for the new version; put the previous one back when it does not come."""
        try:
            restarted = self._service.restart()
        except UpgradeError as e:
            self._say(str(e))
            restarted = None
        if restarted is False:
            self._say(f"{target} is installed; restart Muninn (muninn serve) to run it")
            return 0
        if restarted and self._serves(target):
            self._say(f"upgraded: {previous} → {target}, running")
            return 0
        self._say(f"{target} did not come up; putting {previous} back")
        try:
            self._installation.install(f"{PACKAGE}=={previous}")
        except UpgradeError as e:
            self._say(f"{previous} could not be put back ({e}): install it by hand, uv tool install --force {PACKAGE}=={previous}")
            return 3
        try:
            self._service.restart()
        except UpgradeError as e:
            self._say(str(e))
        if self._serves(previous):
            self._say(f"{previous} is running again; nothing else changed except the database, which {target} may have "
                      "upgraded (its copy from before is in data/backups)")
        else:
            self._say(f"{previous} did not come up either: the database may be one only {target} reads. Its copy from "
                      f"before is in data/backups: stop Muninn, put that muninn.db in place of data/muninn.db, start it")
        return 3

    def _serves(self, version: str) -> bool:
        until = self._clock() + self._wait_s
        while self._clock() < until:
            if self._served() == version:
                return True
            self._sleep(1)
        return False
