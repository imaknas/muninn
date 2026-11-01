"""The user's reference documents (resume versions) read from local files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from muninn.adapters.postings import html_to_text

log = logging.getLogger(__name__)


def read_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".html", ".htm"):
        return html_to_text(path.read_text(errors="replace"))
    if suffix in (".md", ".txt", ".tex"):
        return path.read_text(errors="replace")
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages).strip()
    raise ValueError(f"unsupported document type: {path.name}")


class FileDocuments:
    def __init__(self, paths: Sequence[Path]):
        self._paths = [Path(p).expanduser() for p in paths]

    def files(self) -> dict[str, Path]:
        """Document name → file, for serving the chosen version to the user."""
        return {p.name: p for p in self._paths if p.is_file()}

    def documents(self) -> list[tuple[str, str]]:
        out = []
        for name, path in self.files().items():
            try:
                text = read_text(path)
            except Exception as e:
                log.warning("cannot read %s: %s", name, e)
                continue
            if text.strip():
                out.append((name, text))
        return out
