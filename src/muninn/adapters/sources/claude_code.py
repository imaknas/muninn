"""Claude Code session activity from transcript files (metadata only).

Only attended activity counts: a human prompt, and whatever the session does
within ATTENDED of the last human prompt. An agent working on its own (a
scheduled `claude -p`, a session started by another session) is not the
user's activity. Entries land in 5-minute buckets per session.

Only metadata fields are read from each line: `type`, `timestamp`, `cwd`,
`sessionId`, and the origin markers that say whether a human typed the prompt
(`turnOrigin`, `entrypoint`, `isMeta`, `toolUseResult` presence). Message text,
tool input/output and titles are never read.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from muninn.core.models import Observation, Sensitivity

from .languages import project_languages

log = logging.getLogger(__name__)

BUCKET = timedelta(minutes=5)
ATTENDED = timedelta(minutes=10)
ACTIVITY_TYPES = {"user", "assistant"}


def is_human_prompt(entry: dict[str, Any]) -> bool:
    if entry.get("type") != "user" or entry.get("isMeta") or "toolUseResult" in entry:
        return False
    origin = entry.get("turnOrigin")
    if origin is not None:
        return origin == "human"
    # older interactive CLI transcripts carry no origin markers
    return entry.get("entrypoint") == "cli" and entry.get("promptSource") is None


def repo_root(cwd: str) -> Path | None:
    p = Path(cwd)
    for candidate in (p, *p.parents):
        try:
            if (candidate / ".git").exists():
                return candidate
        except OSError:
            return None
    return None


def is_home_dir(p: Path) -> bool:
    """/Users/<name> or /home/<name> (this machine's or another's): a session
    started there belongs to no project."""
    return p == Path.home() or (len(p.parts) == 3 and p.parts[1] in ("Users", "home")) or len(p.parts) <= 1


def repo_name(cwd: str, workspace_roots: Sequence[Path]) -> str | None:
    """The project a working directory belongs to: its top-level directory
    under a workspace root (so nested repos count toward their project),
    else the enclosing git repository, else the directory itself. None for a
    home directory, which is not a project."""
    p = Path(cwd)
    if is_home_dir(p):
        return None
    for ws in workspace_roots:
        try:
            rel = p.relative_to(ws)
        except ValueError:
            continue
        if rel.parts:
            return rel.parts[0]
    root = repo_root(cwd)
    return root.name if root is not None else p.name


class ClaudeCodeSource:
    name = "claude_code"

    def __init__(
        self,
        roots: Sequence[Path],
        workspace_roots: Sequence[Path],
        initial_lookback: timedelta = timedelta(days=14),
    ):
        self._roots = [Path(r).expanduser() for r in roots]
        self._ws = [Path(r).expanduser() for r in workspace_roots]
        self._lookback = initial_lookback

    def collect(self, cursor: Any, now: datetime) -> tuple[list[Observation], Any]:
        cursor = cursor if isinstance(cursor, dict) and "files" in cursor else {"files": {}, "human": {}}
        offsets: dict[str, int] = dict(cursor["files"])
        self._human: dict[str, datetime] = {k: datetime.fromisoformat(v) for k, v in cursor["human"].items()}
        cutoff = now - self._lookback
        buckets: dict[tuple[str, datetime], dict[str, Any]] = {}
        seen: set[str] = set()
        for root in self._roots:
            if not root.is_dir():
                continue
            for f in root.rglob("*.jsonl"):
                key = str(f)
                seen.add(key)
                try:
                    st = f.stat()
                except OSError:
                    continue
                start = offsets.get(key, 0)
                if start > st.st_size:  # rewritten
                    start = 0
                if start == 0 and key not in offsets and datetime.fromtimestamp(st.st_mtime, timezone.utc) < cutoff:
                    offsets[key] = st.st_size
                    continue
                if start == st.st_size:
                    continue
                offsets[key] = self._read(f, start, cutoff, buckets)
        # forget files that disappeared and sessions idle past the window
        offsets = {k: v for k, v in offsets.items() if k in seen}
        human = {k: v.isoformat() for k, v in self._human.items() if now - v < ATTENDED}
        observations = [self._observation(sid, ts, b) for (sid, ts), b in buckets.items()]
        return observations, {"files": offsets, "human": human}

    def _read(self, f: Path, start: int, cutoff: datetime, buckets: dict) -> int:
        with f.open("rb") as fh:
            fh.seek(start)
            pos = start
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break  # partial line still being written
                pos += len(raw)
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue
                if entry.get("type") not in ACTIVITY_TYPES:
                    continue
                ts_s, cwd, sid = entry.get("timestamp"), entry.get("cwd"), entry.get("sessionId")
                if not (ts_s and cwd and sid):
                    continue
                try:
                    ts = datetime.fromisoformat(ts_s.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts < cutoff:
                    continue
                human = is_human_prompt(entry)
                if human:
                    self._human[sid] = max(ts, self._human.get(sid, ts))
                last = self._human.get(sid)
                if not human and (last is None or not (timedelta(0) <= ts - last <= ATTENDED)):
                    continue
                bucket_ts = datetime.fromtimestamp(ts.timestamp() // BUCKET.total_seconds() * BUCKET.total_seconds(), timezone.utc)
                b = buckets.setdefault((sid, bucket_ts), {"cwd": cwd, "events": 0, "human_turns": 0})
                b["events"] += 1
                b["human_turns"] += human
            return pos

    def _observation(self, sid: str, ts: datetime, b: dict[str, Any]) -> Observation:
        name = repo_name(b["cwd"], self._ws)
        root = None
        if name is not None:
            root = next((ws / name for ws in self._ws if (ws / name).is_dir()), None) or repo_root(b["cwd"])
        return Observation(
            source=self.name,
            ts=ts,
            kind="claude_activity",
            key=f"claude:{sid}:{int(ts.timestamp())}",
            payload={
                **({"repo": name} if name else {}),
                "langs": project_languages(root) if root else [],
                "session": sid,
                "events": b["events"],
                "human_turns": b["human_turns"],
            },
            sensitivity=Sensitivity.PERSONAL,
        )
