"""The collectors that send what the user does on a computer to Muninn (contracts/activity.v1.md), one folder each.
Muninn serves a collector's files itself, so a computer installs it with one line copied from the settings page,
with the address filled in; the ingest token is pasted when the installer asks."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from muninn.core.language import Language

HERE = Path(__file__).resolve().parent
ADDRESS = "__MUNINN_URL__"  # in an installer, replaced by the address Muninn is reached at


@dataclass(frozen=True)
class Collector:
    name: str  # its folder, and the path it is served under: /collectors/<name>/<file>
    label: Callable[[Language], str]  # what the user knows it as
    files: tuple[str, ...]  # what is served; the first is the installer
    note: Callable[[Language], str]  # what it sends, in a sentence

    def file(self, name: str, address: str) -> bytes | None:
        """A file of the collector as served (the installer with Muninn's address in it); None for any other name."""
        if name not in self.files:
            return None
        data = (HERE / self.name / name).read_bytes()
        return data.replace(ADDRESS.encode(), address.encode()) if name == self.files[0] else data

    def command(self, address: str) -> str:
        return f"curl -fsSL {address}/collectors/{self.name}/{self.files[0]} | bash"


COLLECTORS = (
    Collector("mac", lambda words: words("collectors.mac"), ("install.sh", "muninn_agent.py"),
              lambda words: words("collectors.mac_note")),
)
