"""Coding-agent session activity (Claude Code, Codex) from their session files, metadata only.

Only attended activity counts: a human prompt, and whatever the session does
within ATTENDED of the last human prompt. An agent working on its own (a
scheduled `claude -p`, `codex exec`, a session started by another program or
session) is not the user's activity. Entries land in 5-minute buckets per session.

What each line is, the format of its agent says (agent_sessions.py); only when,
which session and folder, and whether a person typed it are used here. Message
text, tool input/output and titles are never kept.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from muninn.core.models import Observation, Sensitivity

from .agent_sessions import FORMATS, SessionFormat, format_of
from .languages import project_languages

log = logging.getLogger(__name__)

BUCKET = timedelta(minutes=5)
ATTENDED = timedelta(minutes=10)


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


class AgentActivitySource:
    name = "claude_code"  # the name its reading position has been kept under since it read Claude Code alone

    def __init__(
        self,
        roots: Sequence[Path],
        workspace_roots: Sequence[Path],
        initial_lookback: timedelta = timedelta(days=14),
        formats: tuple[SessionFormat, ...] = FORMATS,
    ):
        self._roots = [Path(r).expanduser() for r in roots]
        self._ws = [Path(r).expanduser() for r in workspace_roots]
        self._lookback = initial_lookback
        self._formats = formats

    def collect(self, cursor: Any, now: datetime) -> tuple[list[Observation], Any]:
        cursor = cursor if isinstance(cursor, dict) and "files" in cursor else {"files": {}, "human": {}}
        offsets: dict[str, int] = dict(cursor["files"])
        contexts: dict[str, dict[str, Any]] = dict(cursor.get("contexts") or {})  # what a file's later lines leave out
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
                known = format_of(f, self._formats)
                if known is None:
                    offsets[key] = st.st_size  # no agent's format: not read again until it grows
                    continue
                fmt, ctx = known
                ctx = contexts.get(key, ctx) if start else ctx
                offsets[key] = self._read(f, start, cutoff, buckets, fmt, ctx)
                if ctx:
                    contexts[key] = ctx
        # forget files that disappeared and sessions idle past the window
        offsets = {k: v for k, v in offsets.items() if k in seen}
        human = {k: v.isoformat() for k, v in self._human.items() if now - v < ATTENDED}
        observations = [self._observation(agent, sid, ts, b) for (agent, sid, ts), b in buckets.items()]
        return observations, {"files": offsets, "human": human, "contexts": {k: v for k, v in contexts.items() if k in seen}}

    def _read(self, f: Path, start: int, cutoff: datetime, buckets: dict, fmt: SessionFormat, ctx: dict[str, Any]) -> int:
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
                line = fmt.line(entry, ctx) if isinstance(entry, dict) else None
                if line is None or line.ts < cutoff:
                    continue
                sid, ts = line.session, line.ts
                if line.human:
                    self._human[sid] = max(ts, self._human.get(sid, ts))
                last = self._human.get(sid)
                if not line.human and (last is None or not (timedelta(0) <= ts - last <= ATTENDED)):
                    continue
                bucket_ts = datetime.fromtimestamp(ts.timestamp() // BUCKET.total_seconds() * BUCKET.total_seconds(), timezone.utc)
                b = buckets.setdefault((fmt.agent, sid, bucket_ts), {"cwd": line.cwd, "kind": fmt.kind, "events": 0, "human_turns": 0})
                b["events"] += 1
                b["human_turns"] += line.human
            return pos

    def _observation(self, agent: str, sid: str, ts: datetime, b: dict[str, Any]) -> Observation:
        name = repo_name(b["cwd"], self._ws)
        root = None
        if name is not None:
            root = next((ws / name for ws in self._ws if (ws / name).is_dir()), None) or repo_root(b["cwd"])
        claude = agent == "claude_code"  # kept as it always was, so what is held already reads the same
        return Observation(
            source=self.name,
            ts=ts,
            kind=b["kind"],
            key=f"{'claude' if claude else agent}:{sid}:{int(ts.timestamp())}",
            payload={
                **({"repo": name} if name else {}),
                **({} if claude else {"agent": agent}),
                "langs": project_languages(root) if root else [],
                "session": sid,
                "events": b["events"],
                "human_turns": b["human_turns"],
            },
            sensitivity=Sensitivity.PERSONAL,
        )
