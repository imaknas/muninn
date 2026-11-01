"""Conversation text from Claude Code transcripts, for reflection.

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

from .claude_code import is_human_prompt, repo_name

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


def message_text(content: Any) -> str:
    """Only plain text blocks: no tool_use, tool_result, thinking or images."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


class TranscriptTextSource:
    def __init__(self, roots: Sequence[Path], workspace_roots: Sequence[Path], lookback: timedelta = timedelta(days=60)):
        self._roots = [Path(r).expanduser() for r in roots]
        self._ws = [Path(r).expanduser() for r in workspace_roots]
        self._lookback = lookback

    def collect(self, cursor: Any, now: datetime) -> tuple[list[Turn], Any]:
        cursor = cursor if isinstance(cursor, dict) else {}
        offsets: dict[str, int] = dict(cursor.get("files", {}))
        attended: set[str] = set(cursor.get("attended", []))
        cutoff = now - self._lookback
        turns: list[Turn] = []
        seen: set[str] = set()
        for root in self._roots:
            if not root.is_dir():
                continue
            for f in root.rglob("*.jsonl"):
                if "subagents" in f.parts:
                    continue  # agents' own side work
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
                offsets[key] = self._read(f, start, cutoff, attended, turns)
        offsets = {k: v for k, v in offsets.items() if k in seen}
        return turns, {"files": offsets, "attended": sorted(attended)[-5000:]}

    def _read(self, f: Path, start: int, cutoff: datetime, attended: set[str], out: list[Turn]) -> int:
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
                kind, sid, ts_s = e.get("type"), e.get("sessionId"), e.get("timestamp")
                if kind not in ("user", "assistant") or not (sid and ts_s):
                    continue
                try:
                    ts = datetime.fromisoformat(ts_s.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if ts < cutoff:
                    continue
                if kind == "user":
                    if not is_human_prompt(e):
                        continue
                    attended.add(sid)
                elif sid not in attended:
                    continue  # replies in sessions the user never typed into
                text = clean(message_text((e.get("message") or {}).get("content")))
                if not text:
                    continue
                cwd = e.get("cwd") or ""
                out.append(Turn(
                    key=f"{sid}:{e.get('uuid') or ts_s}",
                    session=sid,
                    ts=ts,
                    role=kind,
                    project=repo_name(cwd, self._ws) if cwd else None,
                    text=text,
                ))
            return pos
