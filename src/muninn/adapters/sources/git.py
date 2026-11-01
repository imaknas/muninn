"""Commit metadata from every repository under the workspace roots.

Collected: repo directory name, author time, per-extension file/line counts,
and whether a Co-Authored-By trailer names an AI assistant. Such commits are
recorded as `agent_commit`: they show the project moved, but are not counted
as the user's own hands-on time (attended assistant sessions cover that).
Never collected: commit message text, file paths, diffs.
"""

from __future__ import annotations

import logging
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from muninn.core.models import Observation, Sensitivity

from .languages import language_of

log = logging.getLogger(__name__)

SEP_RECORD, SEP_FIELD = "\x1e", "\x1f"
AI_COAUTHOR_MARKERS = ("noreply@anthropic.com", "claude", "copilot", "openai", "codex", "cursor")


class GitSource:
    name = "git"

    def __init__(
        self,
        roots: Sequence[Path],
        author_emails: Sequence[str],
        initial_lookback: timedelta = timedelta(days=14),
        exclude: Sequence[str] = (),
    ):
        self._roots = [Path(r).expanduser() for r in roots]
        self._emails = {e.lower() for e in author_emails}
        self._lookback = initial_lookback
        self._exclude = set(exclude)

    def repos(self) -> list[Path]:
        out = []
        for root in self._roots:
            if not root.is_dir():
                continue
            for d in sorted(root.iterdir()):
                # a .git file (not a directory) is a worktree of another repository: its commits are
                # that repository's, already read there (log --all), so it is not a project of its own
                if d.name in self._exclude or not (d / ".git").is_dir():
                    continue
                out.append(d)
        return out

    def collect(self, cursor: Any, now: datetime) -> tuple[list[Observation], Any]:
        cursor = dict(cursor or {})
        observations: list[Observation] = []
        for repo in self.repos():
            last = cursor.get(repo.name)
            since = (
                datetime.fromtimestamp(last, timezone.utc) - timedelta(hours=1)  # overlap; keys dedupe
                if last
                else now - self._lookback
            )
            try:
                commits = self._log(repo, since)
            except (subprocess.SubprocessError, OSError) as e:
                log.warning("git log failed in %s: %s", repo, e)
                continue
            for c in commits:
                observations.append(c)
                cursor[repo.name] = max(cursor.get(repo.name) or 0, int(c.ts.timestamp()))
        return observations, cursor

    def _log(self, repo: Path, since: datetime) -> list[Observation]:
        out = subprocess.run(
            [
                "git", "-C", str(repo), "log", "--all", "--no-merges", "--numstat",
                f"--since={int(since.timestamp())}",
                f"--format={SEP_RECORD}%H{SEP_FIELD}%at{SEP_FIELD}%ae{SEP_FIELD}%(trailers:key=Co-Authored-By,valueonly,separator=%x2C)",
            ],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        result = []
        for record in out.split(SEP_RECORD)[1:]:
            head, _, body = record.partition("\n")
            sha, at, email, coauthors = head.split(SEP_FIELD)
            ai = any(m in coauthors.lower() for m in AI_COAUTHOR_MARKERS)
            if self._emails and email.lower() not in self._emails:
                continue
            exts: dict[str, dict[str, int]] = defaultdict(lambda: {"files": 0, "added": 0, "deleted": 0})
            for line in body.strip().splitlines():
                parts = line.split("\t")
                if len(parts) != 3:
                    continue
                added, deleted, path = parts
                ext = Path(path.split(" => ")[-1].rstrip("}")).suffix.lower() or "(none)"
                e = exts[ext]
                e["files"] += 1
                e["added"] += int(added) if added.isdigit() else 0
                e["deleted"] += int(deleted) if deleted.isdigit() else 0
            langs = sorted({lang for ext in exts if (lang := language_of(ext))})
            result.append(
                Observation(
                    source=self.name,
                    ts=datetime.fromtimestamp(int(at), timezone.utc),
                    kind="agent_commit" if ai else "commit",
                    key=f"git:{repo.name}:{sha}",
                    payload={
                        "repo": repo.name,
                        "files": sum(e["files"] for e in exts.values()),
                        "added": sum(e["added"] for e in exts.values()),
                        "deleted": sum(e["deleted"] for e in exts.values()),
                        "exts": dict(exts),
                        "langs": langs,
                    },
                    sensitivity=Sensitivity.PERSONAL,
                )
            )
        return result
