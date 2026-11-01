"""The files the user sends, kept as plain files in one folder of the data directory."""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any

from muninn.core.language import Language
from muninn.core.models import Attachment

KINDS = {  # what may be sent, by type: photos, PDFs, plain text
    "image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf", "text/plain", "text/markdown", "text/csv",
}
MAX_BYTES = 10 * 1024 * 1024


class FileUploads:
    def __init__(self, folder: Path, *, words: Language):
        self.words = words
        self._dir = folder

    def save(self, name: str, media_type: str, data: bytes) -> dict[str, Any]:
        """ValueError for a type not taken, an empty file or one over 10 MB."""
        media_type = media_type.split(";")[0].strip().lower()
        if media_type not in KINDS:
            raise ValueError(self.words("uploads.kind_refused"))
        if not data:
            raise ValueError(self.words("uploads.empty"))
        if len(data) > MAX_BYTES:
            raise ValueError(self.words("uploads.too_big"))
        self._dir.mkdir(parents=True, exist_ok=True)
        uid = secrets.token_hex(8)
        clean = re.sub(r"[^\w.\-]+", "_", Path(name or "file").name)[:80] or "file"
        (self._dir / f"{uid}.bin").write_bytes(data)
        meta = {"id": uid, "name": clean, "media_type": media_type, "size": len(data)}
        (self._dir / f"{uid}.json").write_text(json.dumps(meta, ensure_ascii=False))
        return meta

    def get(self, upload_id: str) -> Attachment | None:
        if not re.fullmatch(r"[0-9a-f]{16}", upload_id or ""):
            return None
        meta, body = self._dir / f"{upload_id}.json", self._dir / f"{upload_id}.bin"
        if not meta.exists() or not body.exists():
            return None
        m = json.loads(meta.read_text())
        return Attachment(m["name"], m["media_type"], body.read_bytes(), m["id"])
