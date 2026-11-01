"""Conversation text from coding-agent sessions (Claude Code, Codex), for reflection.

Keeps what the user typed and the assistant's written replies, in sessions
where the user actually took part. Leaves out tool calls and their output
(files read, command results), the assistant's hidden reasoning, injected
system text, and sessions run entirely by agents.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

from muninn.core.models import Turn

from .agent_activity import repo_name
from .agent_sessions import FORMATS, SessionFormat, format_of

MAX_TURN_CHARS = 4000
INJECTED = re.compile(
    r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+|user-prompt-submit-hook|cross-session-message)\b.*?</\1>",
    re.S,
)


def clean(text: str) -> str:
    text = INJECTED.sub("", text).strip()
    if len(text) > MAX_TURN_CHARS:
        text = text[: MAX_TURN_CHARS // 2] + "\n[…]\n" + text[-MAX_TURN_CHARS // 2 :]
    return text


class TranscriptTextSource:
    def __init__(self, roots: Sequence[Path], workspace_roots: Sequence[Path], lookback: timedelta = timedelta(days=60),
                 formats: tuple[SessionFormat, ...] = FORMATS):
        self._roots = [Path(r).expanduser() for r in roots]
        self._ws = [Path(r).expanduser() for r in workspace_roots]
        self._lookback = lookback
        self._formats = formats

    def collect(self, cursor: Any, now: datetime) -> tuple[list[Turn], Any]:
        cursor = cursor if isinstance(cursor, dict) else {}
        offsets: dict[str, int] = dict(cursor.get("files", {}))
        attended: set[str] = set(cursor.get("attended", []))
        contexts: dict[str, dict[str, Any]] = dict(cursor.get("contexts") or {})  # what a file's later lines leave out
        cutoff = now - self._lookback
        turns: list[Turn] = []
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
                if start > st.st_size:
                    start = 0
                if key not in offsets and datetime.fromtimestamp(st.st_mtime, timezone.utc) < cutoff:
                    offsets[key] = st.st_size
                    continue
                if start == st.st_size:
                    continue
                known = format_of(f, self._formats)
                if known is None or known[0].side_work(f):  # no agent's format, or an agent's own side work
                    offsets[key] = st.st_size
                    continue
                fmt, ctx = known
                ctx = contexts.get(key, ctx) if start else ctx
                offsets[key] = self._read(f, start, cutoff, attended, turns, fmt, ctx)
                if ctx:
                    contexts[key] = ctx
        offsets = {k: v for k, v in offsets.items() if k in seen}
        return turns, {"files": offsets, "attended": sorted(attended)[-5000:],
                       "contexts": {k: v for k, v in contexts.items() if k in seen}}

    def _read(self, f: Path, start: int, cutoff: datetime, attended: set[str], out: list[Turn], fmt: SessionFormat,
              ctx: dict[str, Any]) -> int:
        with f.open("rb") as fh:
            fh.seek(start)
            pos = start
            for raw in fh:
                if not raw.endswith(b"\n"):
                    break
                pos += len(raw)
                try:
                    e = json.loads(raw)
                except ValueError:
                    continue
                line = fmt.line(e, ctx) if isinstance(e, dict) else None
                if line is None or line.role is None or line.ts < cutoff:
                    continue
                if line.role == "user":
                    attended.add(line.session)
                elif line.session not in attended:
                    continue  # replies in sessions the user never typed into
                text = clean(line.text)
                if not text:
                    continue
                out.append(Turn(
                    key=f"{line.session}:{line.key}",
                    session=line.session,
                    ts=line.ts,
                    role=line.role,
                    project=repo_name(line.cwd, self._ws) if line.cwd else None,
                    text=text,
                ))
            return pos
