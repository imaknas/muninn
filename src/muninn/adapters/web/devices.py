"""Device binding for the PWA. Being on the tailnet is not enough: a device
must be paired once with a short-lived code printed by `muninn pair` on the
server. Pairing issues a random device token (kept only as a hash here) that the
PWA presents as an HttpOnly cookie."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O/1/I/L
CODE_TTL = timedelta(minutes=10)
MAX_ATTEMPTS = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
  created TEXT NOT NULL, last_seen TEXT, revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pairing (code_hash TEXT PRIMARY KEY, expires TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
"""


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_code(code: str) -> str:
    return "".join(c for c in code.upper() if c in ALPHABET)


class DeviceRegistry:
    def __init__(self, db_path: str | Path):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=30000")
        self._db.executescript(SCHEMA)

    def new_pairing_code(self) -> str:
        raw = "".join(secrets.choice(ALPHABET) for _ in range(8))
        with self._lock:
            self._db.execute("DELETE FROM pairing WHERE expires < ?", (_now().isoformat(),))
            self._db.execute("INSERT INTO pairing VALUES (?,?,0)", (_h(raw), (_now() + CODE_TTL).isoformat()))
        return f"{raw[:4]}-{raw[4:]}"

    def pair(self, code: str, name: str) -> str | None:
        """Consumes a valid code and returns a new device token, else None.
        Every failed attempt counts against all live codes, so guessing is
        capped at MAX_ATTEMPTS tries per code."""
        with self._lock:
            now = _now().isoformat()
            self._db.execute("DELETE FROM pairing WHERE expires < ? OR attempts >= ?", (now, MAX_ATTEMPTS))
            row = self._db.execute("SELECT code_hash FROM pairing WHERE code_hash=?", (_h(normalize_code(code)),)).fetchone()
            if row is None:
                self._db.execute("UPDATE pairing SET attempts = attempts + 1")
                return None
            self._db.execute("DELETE FROM pairing WHERE code_hash=?", (row["code_hash"],))
            token = secrets.token_urlsafe(32)
            self._db.execute(
                "INSERT INTO devices (name, token_hash, created) VALUES (?,?,?)", (name[:60] or "device", _h(token), now)
            )
            return token

    def any_paired(self) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM devices WHERE revoked = 0 LIMIT 1").fetchone() is not None

    def check(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self._lock:
            row = self._db.execute("SELECT * FROM devices WHERE token_hash=? AND revoked=0", (_h(token),)).fetchone()
            if row is None:
                return None
            self._db.execute("UPDATE devices SET last_seen=? WHERE id=?", (_now().isoformat(), row["id"]))
            return {"id": row["id"], "name": row["name"], "created": row["created"]}

    def devices(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) | {"token_hash": None} for r in self._db.execute("SELECT * FROM devices ORDER BY id")]

    def revoke(self, device_id: int) -> bool:
        with self._lock:
            return self._db.execute("UPDATE devices SET revoked=1 WHERE id=?", (device_id,)).rowcount == 1
