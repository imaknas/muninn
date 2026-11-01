"""The running version, read from the git clone Muninn runs from, and the deploy log beside its data."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)
TIMEOUT_S = 10


class GitVersion:
    unknown: str | None = None

    def __init__(self, repo: Path, deploy_log: Path):
        """`repo`: the checkout this code runs from; `deploy_log`: where a deploy writes what it did."""
        self._repo = repo
        self._log = deploy_log

    @classmethod
    def at(cls, repo: Path, deploy_log: Path) -> GitVersion | None:
        """The running version of that checkout; None when it is not one git can read (or there is no git)."""
        try:
            done = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            return None
        return cls(repo, deploy_log) if done.returncode == 0 else None

    def head(self) -> str | None:
        try:
            done = subprocess.run(["git", "-C", str(self._repo), "rev-parse", "HEAD"], capture_output=True, text=True,
                                  timeout=TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("git rev-parse failed: %s", e)
            return None
        return (done.stdout.strip() or None) if done.returncode == 0 else None

    def contains(self, commit: str) -> bool | None:
        if not commit or commit.startswith("-"):
            return None
        try:
            done = subprocess.run(["git", "-C", str(self._repo), "merge-base", "--is-ancestor", commit, "HEAD"],
                                  capture_output=True, timeout=TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as e:
            log.warning("git merge-base failed: %s", e)
            return None
        return {0: True, 1: False}.get(done.returncode)  # anything else: a commit this history does not know

    def deploy_lines(self) -> list[str]:
        try:
            return [x for x in self._log.read_text(encoding="utf-8").splitlines() if x.strip()]
        except FileNotFoundError:
            return []
        except OSError as e:
            log.warning("deploy log unreadable: %s", e)
            return []
