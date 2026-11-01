"""Coding-agent sessions on this computer: each agent writes its sessions as JSON lines in a format of its own.

A format turns a file's lines into what the readers use: when something happened, in which session and folder,
whether it was a prompt the user typed, and the text said (for reflection). Which format a file is in is told by its
first line, so a folder of any agent's sessions is read without saying which agent it is. The first line also gives
what a format's later lines leave out (Codex: the session and its folder), so a file is read on from where the last
read stopped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Line:
    ts: datetime
    session: str
    cwd: str
    human: bool  # a prompt the user typed
    role: str | None  # "user" or "assistant" when it is said text; None for other activity (a tool call, its output)
    text: str  # what was said: empty for other activity
    key: str  # the line, within its session


class SessionFormat(Protocol):
    agent: str  # what the activity it shows is said to come from ("claude_code", "codex")
    kind: str  # the observation kind of that activity

    def recognizes(self, first: dict[str, Any]) -> bool:
        """Whether a file whose first line is `first` is this agent's."""
        ...

    def context(self, first: dict[str, Any]) -> dict[str, Any]:
        """What the file's later lines leave out, from its first line (kept up to date by `line`)."""
        ...

    def line(self, entry: dict[str, Any], ctx: dict[str, Any]) -> Line | None:
        """One line as the readers use it; None for a line that is neither activity nor said text."""
        ...

    def side_work(self, path: Path) -> bool:
        """A file of an agent's own side work, never a conversation with the user."""
        ...


def first_line(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as fh:
            entry = json.loads(fh.readline() or b"{}")
    except (OSError, ValueError):
        return None
    return entry if isinstance(entry, dict) else None


def format_of(path: Path, formats: tuple[SessionFormat, ...]) -> tuple[SessionFormat, dict[str, Any]] | None:
    """The format a file is in and its context; None when no format knows it."""
    first = first_line(path)
    if not first:
        return None
    for f in formats:
        if f.recognizes(first):
            return f, f.context(first)
    return None


def when(ts: Any) -> datetime | None:
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


# ── Claude Code ─────────────────────────────────────────────────────────────


def is_human_prompt(entry: dict[str, Any]) -> bool:
    if entry.get("type") != "user" or entry.get("isMeta") or "toolUseResult" in entry:
        return False
    origin = entry.get("turnOrigin")
    if origin is not None:
        return origin == "human"
    # older interactive CLI transcripts carry no origin markers
    return entry.get("entrypoint") == "cli" and entry.get("promptSource") is None


def message_text(content: Any) -> str:
    """Only plain text blocks: no tool_use, tool_result, thinking or images."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


class ClaudeCode:
    """Claude Code (~/.claude/projects): every line carries its session and folder; a prompt says who typed it."""

    agent, kind = "claude_code", "claude_activity"

    def recognizes(self, first: dict[str, Any]) -> bool:
        return "sessionId" in first or first.get("type") == "summary"

    def context(self, first: dict[str, Any]) -> dict[str, Any]:
        return {}

    def line(self, entry: dict[str, Any], ctx: dict[str, Any]) -> Line | None:
        kind, ts, cwd, sid = entry.get("type"), when(entry.get("timestamp")), entry.get("cwd"), entry.get("sessionId")
        if kind not in ("user", "assistant") or ts is None or not (cwd and sid):
            return None
        human = is_human_prompt(entry)
        said = human or kind == "assistant"
        return Line(ts, sid, cwd, human, kind if said else None,
                    message_text((entry.get("message") or {}).get("content")) if said else "", str(entry.get("uuid") or entry["timestamp"]))

    def side_work(self, path: Path) -> bool:
        return "subagents" in path.parts


# ── Codex ───────────────────────────────────────────────────────────────────

# the clients a person types into; a session another program started (`exec`, a custom client, a sub-agent) is not
ATTENDED_SOURCES = ("cli", "vscode")
INJECTED = re.compile(r"^\s*<[a-z_]+>")  # context Codex adds as a user message (environment_context, instructions …)


def _texts(content: Any, kind: str) -> str:
    return "\n".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == kind)


class Codex:
    """Codex (~/.codex/sessions): the first line holds the session, its folder and who started it; a turn may move to
    another folder. A session counts as the user's own only when it was started in an official client (its
    originator is Codex's own) from the command line or an editor, not by another program or as a sub-agent."""

    agent, kind = "codex", "agent_activity"

    def recognizes(self, first: dict[str, Any]) -> bool:
        return first.get("type") == "session_meta" and isinstance(first.get("payload"), dict) and "id" in first["payload"]

    def context(self, first: dict[str, Any]) -> dict[str, Any]:
        p = first["payload"]
        attended = (p.get("source") in ATTENDED_SOURCES and p.get("thread_source") in (None, "user")
                    and str(p.get("originator") or "").lower().startswith("codex"))
        return {"session": str(p["id"]), "cwd": str(p.get("cwd") or ""), "attended": attended}

    def line(self, entry: dict[str, Any], ctx: dict[str, Any]) -> Line | None:
        p = entry.get("payload")
        if not isinstance(p, dict):
            return None
        if entry.get("type") == "turn_context":
            ctx["cwd"] = str(p.get("cwd") or ctx["cwd"])
            return None
        ts = when(entry.get("timestamp"))
        if entry.get("type") != "response_item" or ts is None or not ctx["cwd"]:
            return None
        key = str(p.get("id") or f"{entry.get('timestamp')}:{entry.get('ordinal')}")
        kind, role = p.get("type"), p.get("role")
        if kind == "additional_tools" or role in ("developer", "system"):
            return None  # what Codex gives the model, not anything done
        if kind == "message" and role == "user":
            text = _texts(p.get("content"), "input_text")
            kinds = (p.get("internal_chat_message_metadata_passthrough") or {}).get("content_item_kinds")
            typed = "user.text" in kinds if isinstance(kinds, list) else not INJECTED.match(text)
            if not typed:
                return None  # context Codex added (the environment, instructions)
            human = bool(ctx["attended"])
            return Line(ts, ctx["session"], ctx["cwd"], human, "user" if human else None, text if human else "", key)
        if kind == "message" and role == "assistant":
            # narration between tool calls is not what was said to the user
            said = "" if p.get("phase") == "commentary" else _texts(p.get("content"), "output_text")
            return Line(ts, ctx["session"], ctx["cwd"], False, "assistant", said, key)
        return Line(ts, ctx["session"], ctx["cwd"], False, None, "", key)  # a tool call, its output, reasoning

    def side_work(self, path: Path) -> bool:
        return False  # a sub-agent's session says so in its first line


FORMATS: tuple[SessionFormat, ...] = (Codex(), ClaudeCode())
