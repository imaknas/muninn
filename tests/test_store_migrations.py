"""The database carries its schema version; an older one is copied, then brought up to date, once, under the lock."""

from __future__ import annotations

import json
import multiprocessing
import sqlite3

import pytest

from muninn.adapters import store_sqlite
from muninn.adapters.store_sqlite import SCHEMA_VERSION, SqliteStore, StoreTooNew


def meta(path) -> dict[str, str]:
    with sqlite3.connect(path) as db:
        return dict(db.execute("SELECT key, value FROM meta").fetchall())


def from_before_versions(path) -> None:
    """A database as versions before the schema version left it: no meta, and handoffs still in one state row."""
    SqliteStore(path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE meta")
        db.execute("INSERT INTO state (key, value) VALUES ('handoffs', ?)", (json.dumps(
            [{"id": 7, "ts": "2026-10-20T09:00:00+08:00", "status": "pending", "project": "garden", "fact": "排水管 30 公分"}]),))


def test_a_new_database_starts_at_this_version_without_a_copy(tmp_path):
    SqliteStore(tmp_path / "muninn.db")
    assert meta(tmp_path / "muninn.db") == {"schema_version": str(SCHEMA_VERSION), "min_readable": "1"}
    assert not (tmp_path / "backups").exists()


def test_an_older_database_is_copied_then_brought_up_to_date_once(tmp_path):
    path = tmp_path / "muninn.db"
    from_before_versions(path)
    s = SqliteStore(path)
    [copy] = (tmp_path / "backups").glob("muninn-*-v0.db")
    with sqlite3.connect(copy) as db:  # the copy is the database as it was
        assert db.execute("SELECT value FROM state WHERE key = 'handoffs'").fetchone()
    assert [h["id"] for h in s.handoffs()] == [7] and meta(path)["schema_version"] == str(SCHEMA_VERSION)
    SqliteStore(path)
    assert len(list((tmp_path / "backups").glob("*.db"))) == 1  # up to date: no new copy


def test_only_the_newest_copies_are_kept(tmp_path, monkeypatch):
    path = tmp_path / "muninn.db"
    SqliteStore(path)
    for i in range(store_sqlite.KEEP_BACKUPS + 2):
        (tmp_path / "backups").mkdir(exist_ok=True)
        (tmp_path / "backups" / f"muninn-2026010{i}-000000-v0.db").write_bytes(b"")
    SqliteStore(path)._backup(0)
    assert len(list((tmp_path / "backups").glob("muninn-*.db"))) == store_sqlite.KEEP_BACKUPS


def test_a_database_a_newer_version_cannot_be_read_past_is_refused(tmp_path):
    path = tmp_path / "muninn.db"
    SqliteStore(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE meta SET value = ? WHERE key = 'min_readable'", (str(SCHEMA_VERSION + 1),))
    with pytest.raises(StoreTooNew, match="newer Muninn"):
        SqliteStore(path)


def test_a_version_ahead_but_still_readable_is_opened_and_left_as_it_is(tmp_path):
    path = tmp_path / "muninn.db"
    SqliteStore(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION + 3),))
    SqliteStore(path)
    assert meta(path)["schema_version"] == str(SCHEMA_VERSION + 3)  # not lowered by an older version opening it


def _open(path, out):
    s = SqliteStore(path)
    out.put(sorted(h["id"] for h in s.handoffs()))


def test_two_processes_opening_an_older_database_at_once_both_get_it_up_to_date(tmp_path):
    path = tmp_path / "muninn.db"
    from_before_versions(path)
    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    procs = [ctx.Process(target=_open, args=(str(path), out)) for _ in range(2)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert [p.exitcode for p in procs] == [0, 0]
    assert [out.get(timeout=5), out.get(timeout=5)] == [[7], [7]]  # the handoff moved once, read by both


def test_muninn_stops_with_the_reason_on_a_database_it_cannot_read(tmp_path, caplog):
    from muninn.main import main

    SqliteStore(tmp_path / "muninn.db")
    with sqlite3.connect(tmp_path / "muninn.db") as db:
        db.execute("UPDATE meta SET value = '99' WHERE key = 'min_readable'")
    assert main(["--data", str(tmp_path), "--dry-run", "status"]) == 2
    assert "newer Muninn" in caplog.text
