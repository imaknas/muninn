"""Project records maintained outside Muninn, read from files.

- a to-do file: '## <owner heading>' sections, '### <project>' subsections,
  '- [ ] item' lines; which heading means which owner comes from config;
- one state summary per project at a path pattern containing '{name}';
- weekly statistics files named '<ISO week>.json' in a directory.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Mapping

from muninn.core.models import ExternalTodo

log = logging.getLogger(__name__)
ITEM = re.compile(r"^\s*-\s+\[( |x|X)\]\s+(.*\S)\s*$")


def _owner(heading: str, owners: Mapping[str, str]) -> str | None:
    return next((o for key, o in owners.items() if key in heading), None)


def parse_todos(text: str, owners: Mapping[str, str]) -> list[ExternalTodo]:
    """Open items only. A section whose heading matches no configured owner
    counts as 'pending'."""
    out: list[ExternalTodo] = []
    owner, project = "pending", None
    for line in text.splitlines():
        if line.startswith("## "):
            owner = _owner(line[3:].strip(), owners) or "pending"
            project = None
        elif line.startswith("### "):
            project = line[4:].strip()
        elif project and (m := ITEM.match(line)) and m.group(1) == " ":
            out.append(ExternalTodo(project, owner, m.group(2)))
    return out


def unowned_headings(text: str, owners: Mapping[str, str]) -> list[str]:
    """'## ' headings with open items that match no configured owner."""
    out: list[str] = []
    heading = None
    for line in text.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
        elif heading and heading not in out and (m := ITEM.match(line)) and m.group(1) == " " and _owner(heading, owners) is None:
            out.append(heading)
    return out


def owners_match(text: str, owners: Mapping[str, str]) -> bool:
    """False when the file has open items but none sits under a configured
    owner heading: the producer renamed its headings, and every item would
    silently read as 'pending'."""
    has_items = any((m := ITEM.match(line)) and m.group(1) == " " for line in text.splitlines())
    return not has_items or any(
        line.startswith("## ") and _owner(line[3:].strip(), owners) is not None for line in text.splitlines()
    )


class FileProjectRecords:
    def __init__(
        self,
        todos_path: str | None,
        owners: Mapping[str, str],
        states_pattern: str | None,
        stats_dir: str | None,
    ):
        self._todos = Path(todos_path).expanduser() if todos_path else None
        self._owners = dict(owners)
        self._states = states_pattern
        self._stats = Path(stats_dir).expanduser() if stats_dir else None
        self._owners_ok = True  # warn once when the headings stop matching, again after they recover

    def _state_path(self, name: str) -> Path | None:
        if not self._states or "/" in name or name.startswith("."):
            return None
        return Path(self._states.replace("{name}", name)).expanduser()

    def projects(self) -> set[str]:
        names = {t.project for t in self.todos()}
        if self._states and "{name}" in self._states:
            parent = Path(self._states.split("{name}")[0]).expanduser()
            if parent.is_dir():
                names |= {d.name for d in parent.iterdir() if (p := self._state_path(d.name)) and p.is_file()}
        return names

    def todos(self) -> list[ExternalTodo]:
        if not self._todos or not self._todos.is_file():
            return []
        try:
            text = self._todos.read_text()
        except OSError as e:
            log.warning("cannot read to-do file: %s", e)
            return []
        ok = owners_match(text, self._owners)
        if not ok and self._owners_ok:
            log.warning("to-do file %s: no heading matches external_todo_owners %s (found %s); every item reads as 'pending'",
                        self._todos, sorted(self._owners), unowned_headings(text, self._owners))
        self._owners_ok = ok
        return parse_todos(text, self._owners)

    def state(self, project: str) -> str | None:
        p = self._state_path(project)
        try:
            return p.read_text() if p and p.is_file() else None
        except OSError:
            return None

    def weekly_stats(self, iso_week: str) -> dict[str, Any] | None:
        if not self._stats or not re.fullmatch(r"\d{4}-W\d{2}", iso_week):
            return None
        p = self._stats / f"{iso_week}.json"
        try:
            return json.loads(p.read_text()) if p.is_file() else None
        except (OSError, ValueError):
            return None

