"""Projects as plain folders, for a Muninn with no other system keeping its projects
(contracts/local-projects.v1.md). Only the folders the user lists take part, plus the
sandboxes Muninn opens for ideas. In each project folder:

- TODO.md: '- [ ] item' lines; a '## ' heading with 'agent' in it means an agent can do
  the items below, one with '待定' or 'pending' means a decision comes first, anything
  else (or no heading) means only the user can;
- STATE.md: the project's current state, read whole;
- .muninn/inbox.md: what Muninn hands the project (appended; the project's own agent reads it);
- .muninn/outbox.yaml: what the project tells Muninn, a list of messages.v1 entries
  without `project` (the folder's name is the project).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from muninn.core.models import ExternalTodo
from muninn.core.ports import SandboxRefused
from muninn.core.project_messages import parse
from muninn.core.language import Language

log = logging.getLogger(__name__)
ITEM = re.compile(r"^\s*-\s+\[( |x|X)\]\s+(.*\S)\s*$")
STATE_CHARS = 20_000
SANDBOX_NAME = re.compile(r"[^\w\-]+")


def todo_owner(heading: str) -> str:
    h = heading.lower()
    if "agent" in h:
        return "agent"
    if "待定" in heading or "pending" in h:
        return "pending"
    return "me"


def parse_todo_md(project: str, text: str) -> list[ExternalTodo]:
    """Open items, each with its owner from the heading above it (only the user, when none)."""
    out, owner = [], "me"
    for line in text.splitlines():
        if line.startswith("## "):
            owner = todo_owner(line[3:].strip())
        elif (m := ITEM.match(line)) and m.group(1) == " ":
            out.append(ExternalTodo(project, owner, m.group(2)))
    return out


def own_id(project: str, key: str) -> str:
    """A project's own message id as Muninn keeps it: prefixed with the project, since every folder
    picks its ids alone (two projects may both say `q1`)."""
    return f"{project}:{key}"


def their_id(project: str, key: str) -> str:
    """The id as the project wrote it (Muninn's prefix taken off), for what goes back to it."""
    return key.removeprefix(f"{project}:")


def handoff_entry(h: dict[str, Any], words: Language) -> str:
    """One handoff as the project's agent reads it, with its own ids; `#<id>` marks it delivered."""
    lines = [f"## {str(h.get('ts', ''))[:16].replace('T', ' ')} · {h['effect']} · #{h['id']}"]
    labels = {"item": words("local_projects.item"), "fact": words("local_projects.fact"), "quote": words("local_projects.quote"),
              "why": words("local_projects.why"), "reply_to": words("local_projects.reply_to"), "task": words("local_projects.task")}
    for key, label in labels.items():
        if h.get(key):
            value = their_id(str(h["project"]), str(h[key])) if key in ("reply_to", "task") else h[key]
            lines.append(words("local_projects.line", label=label, value=value))
    return "\n".join(lines) + "\n\n"


class LocalProjects:
    """The project side kept in folders: records, outbox, inbox and sandboxes in one."""

    def __init__(self, folders: Sequence[str | Path], sandbox_root: str | Path | None = None, *, words: Language):
        """`folders`: the projects Muninn may use (no others are read, written or lent);
        `sandbox_root`: where idea sandboxes are opened (None: this side opens none)."""
        self.words = words
        self._folders = [Path(f).expanduser() for f in folders]
        self._sandbox_root = Path(sandbox_root).expanduser() if sandbox_root else None
        names = [f.name for f in self._folders]
        if dupes := sorted({n for n in names if names.count(n) > 1}):
            raise ValueError(f"projects.local.folders: two folders named {dupes}; a project is its folder's name")

    def _paths(self) -> dict[str, Path]:
        found = {f.name: f for f in self._folders if f.is_dir()}
        if self._sandbox_root and self._sandbox_root.is_dir():
            for d in sorted(self._sandbox_root.iterdir()):
                if d.is_dir() and d.name not in found:
                    found[d.name] = d
        return found

    # ── ProjectRecords ──

    def projects(self) -> set[str]:
        return set(self._paths())

    def todos(self) -> list[ExternalTodo]:
        out = []
        for name, path in self._paths().items():
            todo = path / "TODO.md"
            if todo.is_file():
                out += parse_todo_md(name, todo.read_text(encoding="utf-8", errors="replace"))
        return out

    def state(self, project: str) -> str | None:
        path = self._paths().get(project)
        state = path / "STATE.md" if path else None
        return state.read_text(encoding="utf-8", errors="replace")[:STATE_CHARS] if state and state.is_file() else None

    def weekly_stats(self, iso_week: str) -> dict[str, Any] | None:
        return None  # folders keep no conversation statistics

    # ── HandoffOutbox ──

    def deliver(self, handoffs: Sequence[dict[str, Any]]) -> list[int]:
        """Appends each handoff to its project's .muninn/inbox.md (once: an entry already
        there counts as delivered); handoffs for projects not listed stay pending."""
        paths, done = self._paths(), []
        for h in handoffs:
            path = paths.get(str(h.get("project")))
            if path is None:
                continue
            inbox = path / ".muninn" / "inbox.md"
            inbox.parent.mkdir(exist_ok=True)
            seen = inbox.read_text(encoding="utf-8") if inbox.is_file() else ""
            if f" · #{h['id']}\n" not in seen:
                with inbox.open("a", encoding="utf-8") as f:
                    f.write(handoff_entry(h, self.words))
            done.append(int(h["id"]))
        return done

    # ── ProjectInbox ──

    def collect(self) -> list[dict[str, Any]]:
        """Every message in the projects' .muninn/outbox.yaml, checked one by one (a malformed
        one is left out and logged; the rest still arrive). Muninn takes each id once."""
        out = []
        for name, path in self._paths().items():
            outbox = path / ".muninn" / "outbox.yaml"
            if not outbox.is_file():
                continue
            try:
                entries = YAML(typ="safe").load(outbox.read_text(encoding="utf-8")) or []
            except Exception as e:  # a half-written file: try again next time
                log.warning("%s: unreadable outbox: %s", name, e)
                continue
            for raw in entries if isinstance(entries, list) else []:
                if not isinstance(raw, dict):
                    continue
                message = {**raw, "project": name}
                for key in ("id", "task"):  # ids are the project's own: kept apart from other projects'
                    if isinstance(raw.get(key), str) and raw[key].strip():
                        message[key] = own_id(name, raw[key].strip())
                try:
                    parse(message)
                except ValueError as e:
                    log.warning("%s: message %r left out: %s", name, raw.get("id"), e)
                    continue
                out.append(message)
        return out

    # ── Sandboxes ──

    def create(self, name: str, purpose: str) -> str | None:
        """A folder under the sandbox root, with a README saying what it is for. Asked again for the
        same name and purpose (a retry), the same folder; the name taken by something else, refused."""
        if self._sandbox_root is None:
            return None
        slug = SANDBOX_NAME.sub("-", name.strip()).strip("-")[:60] or "idea"
        folder = self._sandbox_root / slug
        readme = f"# {name}\n\n{purpose}\n"
        if folder.exists() or slug in {f.name for f in self._folders}:
            same = (folder / "README.md").is_file() and (folder / "README.md").read_text(encoding="utf-8") == readme
            if not same:
                raise SandboxRefused("taken", detail=str(folder))
            return slug
        (folder / ".muninn").mkdir(parents=True)
        (folder / "README.md").write_text(readme, encoding="utf-8")
        return slug

    def upgrade(self, name: str, rename: str | None) -> str | None:
        """Folders here are not made projects of their own (no repository is created on this side)."""
        return None

    def rename(self, name: str, to: str) -> str | None:
        """Folders here keep the name they were opened under (the user sees the idea's name)."""
        return None
