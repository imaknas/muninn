"""SQLite implementation of the Store port. WAL mode so the web server and the
scheduled CLIs can share one database file.

The database carries its schema version (table `meta`). Every change to it only
adds (a table, a column, a data fix that can run again), so a version from before
a change still reads the database after it; `min_readable` is raised only by a
change an older version cannot read past, and a version older than that refuses
to open the database rather than misread it. The first process to open a database
from an older version makes a copy of it (data/backups/) and brings it up to date
while holding the write lock; any other waits for it."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from muninn.core.ph import OLDER_EXTS, PH, SPEAKS
from muninn.core.models import ActionRecord, Confirmation, InboxItem, Observation, Prepared, Sensitivity, Turn

SCHEMA_VERSION = 1  # raised with every change to the schema or to what the migrations do
MIN_READABLE = 1  # raised only for a change an older version cannot read past
KEEP_BACKUPS = 3  # copies made before a migration, newest first


class StoreTooNew(RuntimeError):
    """The database was brought up to a schema this version of Muninn cannot read."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
  key TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  ts TEXT NOT NULL,
  kind TEXT NOT NULL,
  domain TEXT,
  goal TEXT,
  sensitivity INTEGER NOT NULL,
  payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS obs_ts ON observations(ts);
CREATE INDEX IF NOT EXISTS obs_domain_ts ON observations(domain, ts);
CREATE INDEX IF NOT EXISTS obs_goal_ts ON observations(goal, ts);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS markers (marker TEXT PRIMARY KEY, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS signals (key TEXT, day TEXT, level INTEGER, PRIMARY KEY (key, day));
CREATE TABLE IF NOT EXISTS inbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, domain TEXT NOT NULL, kind TEXT NOT NULL,
  title TEXT NOT NULL, body TEXT NOT NULL, data TEXT NOT NULL,
  signal_key TEXT, level INTEGER NOT NULL DEFAULT 0, read INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pushes (at TEXT NOT NULL, day TEXT NOT NULL, domain TEXT NOT NULL, tier TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS confirmations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL, kind TEXT NOT NULL, question TEXT NOT NULL,
  options TEXT NOT NULL, data TEXT NOT NULL, answer TEXT, answered_at TEXT, key TEXT
);
CREATE TABLE IF NOT EXISTS learned_rules (key TEXT PRIMARY KEY, domain TEXT NOT NULL, goal TEXT);
CREATE TABLE IF NOT EXISTS completed (id TEXT PRIMARY KEY, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS subscriptions (endpoint TEXT PRIMARY KEY, sub TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prepared (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, domain TEXT NOT NULL, target TEXT NOT NULL,
  kind TEXT NOT NULL, title TEXT NOT NULL, data TEXT NOT NULL, status TEXT NOT NULL, prepared_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, domain TEXT NOT NULL, text TEXT NOT NULL,
  undo TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS turns (
  key TEXT PRIMARY KEY, session TEXT NOT NULL, ts TEXT NOT NULL, role TEXT NOT NULL, project TEXT, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS turns_session_ts ON turns(session, ts);
CREATE TABLE IF NOT EXISTS digests (
  session TEXT NOT NULL, until TEXT NOT NULL, project TEXT, data TEXT NOT NULL, at TEXT NOT NULL,
  PRIMARY KEY (session, until)
);
CREATE TABLE IF NOT EXISTS eval_cases (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, task TEXT NOT NULL, system TEXT NOT NULL, data TEXT NOT NULL,
  reply TEXT, backend TEXT, by_local INTEGER, cloud_ok INTEGER NOT NULL, json_mode INTEGER NOT NULL, feedback TEXT
);
CREATE INDEX IF NOT EXISTS eval_cases_ts ON eval_cases(ts);
CREATE TABLE IF NOT EXISTS usage (ts TEXT NOT NULL, backend TEXT NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS usage_ts ON usage(ts);
CREATE TABLE IF NOT EXISTS status_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT, target TEXT, before TEXT, after TEXT, at TEXT
);
CREATE TABLE IF NOT EXISTS handoffs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, status TEXT NOT NULL,
  project TEXT, item TEXT, fact TEXT, data TEXT NOT NULL, changed TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS handoffs_status ON handoffs(status, id);
CREATE TABLE IF NOT EXISTS ph_records (
  seq INTEGER PRIMARY KEY AUTOINCREMENT, ext TEXT NOT NULL, project TEXT NOT NULL, origin TEXT NOT NULL, id TEXT NOT NULL,
  key TEXT NOT NULL, record TEXT NOT NULL, received TEXT NOT NULL, UNIQUE (ext, project, origin, id)
);
CREATE INDEX IF NOT EXISTS ph_records_item ON ph_records(ext, project, key);
CREATE TABLE IF NOT EXISTS tasks (
  key TEXT PRIMARY KEY, n INTEGER NOT NULL UNIQUE, title TEXT NOT NULL, due TEXT, status TEXT NOT NULL,
  context TEXT, links TEXT NOT NULL, created TEXT NOT NULL, closed TEXT, closed_by TEXT, why TEXT
);
CREATE TABLE IF NOT EXISTS task_origins (
  key TEXT NOT NULL, project TEXT NOT NULL, ref TEXT NOT NULL, text TEXT, PRIMARY KEY (key, ref)
);
CREATE INDEX IF NOT EXISTS task_origins_ref ON task_origins(ref);
CREATE TABLE IF NOT EXISTS project_messages (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, project TEXT NOT NULL, type TEXT NOT NULL, text TEXT NOT NULL,
  handoff_id INTEGER, status TEXT NOT NULL, answer TEXT, changed TEXT NOT NULL, context TEXT
);
CREATE TABLE IF NOT EXISTS leases (
  lease_id TEXT PRIMARY KEY, thread TEXT NOT NULL, project TEXT NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL,
  started TEXT NOT NULL, ended TEXT, last_seq INTEGER NOT NULL DEFAULT 0, last_activity TEXT, last_user_at TEXT,
  warn_state TEXT NOT NULL DEFAULT 'none', warned_at TEXT, handover TEXT, end_reason TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS lease_turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT, lease_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lease_turns_lease ON lease_turns(lease_id, id);
CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY, ts TEXT NOT NULL, about_kind TEXT, about_ref TEXT, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS threads_ts ON threads(ts);
CREATE INDEX IF NOT EXISTS threads_about ON threads(about_kind, about_ref, ts);
CREATE TABLE IF NOT EXISTS thread_turns (
  thread TEXT NOT NULL, ix INTEGER NOT NULL, data TEXT NOT NULL, PRIMARY KEY (thread, ix)
);
CREATE TABLE IF NOT EXISTS permission_requests (
  id TEXT PRIMARY KEY, lease_id TEXT, project TEXT, tool TEXT NOT NULL, input_summary TEXT, risk TEXT, question TEXT,
  created TEXT NOT NULL, decided TEXT, decision TEXT, answers TEXT, by TEXT
);
"""


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _parse_ts(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _statements(script: str) -> list[str]:
    """A script's statements, one by one (executescript would commit the transaction they belong to)."""
    out, cur = [], ""
    for line in script.splitlines(keepends=True):
        cur += line
        if sqlite3.complete_statement(cur):
            out.append(cur.strip())
            cur = ""
    return [s for s in out if s]


class SqliteStore:
    def __init__(self, path: str | Path):
        self._path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self._path, check_same_thread=False, timeout=30, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._writes = 0  # this process's writes, for `version`
        if self._path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=30000")
        self._db.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        found, readable = self._meta("schema_version"), self._meta("min_readable")
        if readable is not None and readable > SCHEMA_VERSION:
            raise StoreTooNew(f"{self._path} was brought up to a newer Muninn (it needs schema {readable}; this "
                              f"version reads up to {SCHEMA_VERSION}): run that version, or restore a copy from backups/")
        if (found or 0) < SCHEMA_VERSION and self._path != ":memory:" and self._has_data():
            self._backup(found or 0)  # before the lock: a copy cannot be taken while holding it
        with self._write():
            self._migrate()
            self._set_meta("schema_version", max(found or 0, SCHEMA_VERSION))
            self._set_meta("min_readable", max(readable or 0, MIN_READABLE))

    def _meta(self, key: str) -> int | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return int(row["value"]) if row else None

    def _set_meta(self, key: str, value: int) -> None:
        self._db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (key, str(value)))

    def _has_data(self) -> bool:
        return bool(self._db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name != 'meta' LIMIT 1").fetchone())

    def _backup(self, from_version: int) -> Path:
        """A copy of the database as it is, kept beside it in backups/ (the newest KEEP_BACKUPS)."""
        db = Path(self._path)
        folder = db.parent / "backups"
        folder.mkdir(mode=0o700, exist_ok=True)
        dest = folder / f"{db.stem}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-v{from_version}{db.suffix}"
        with sqlite3.connect(dest) as copy:
            self._db.backup(copy)
        dest.chmod(0o600)
        for old in sorted(folder.glob(f"{db.stem}-*{db.suffix}"), reverse=True)[KEEP_BACKUPS:]:
            old.unlink()
        return dest

    def _migrate(self) -> None:
        """The schema as this version has it, from any earlier one: each step only adds, and can run again."""
        for statement in _statements(SCHEMA):
            self._db.execute(statement)
        self._add_column("project_messages", "context", "TEXT")
        self._add_column("eval_cases", "ms", "INTEGER")
        self._add_column("eval_cases", "verdict", "TEXT")
        self._add_column("eval_cases", "input_tokens", "INTEGER")  # what the provider said the request took
        self._add_column("eval_cases", "output_tokens", "INTEGER")
        self._add_column("leases", "answering", "INTEGER NOT NULL DEFAULT 0")
        self._add_column("leases", "left_at", "TEXT")
        self._add_column("leases", "end_asked", "TEXT")  # when the user said they were done with the talk
        self._add_column("lease_turns", "files", "TEXT")
        self._add_column("lease_turns", "detail", "TEXT")
        self._add_column("permission_requests", "kind", "TEXT")
        self._add_column("permission_requests", "summary", "TEXT")
        self._add_column("permission_requests", "v", "INTEGER NOT NULL DEFAULT 1")
        self._kinds_for_old_requests()
        self._usage_from_cases()
        self._records_as_04()
        self._migrate_handoffs()
        self._migrate_threads()
        self._handover_cards()

    def _q(self, sql: str, args: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _x(self, sql: str, args: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            self._writes += 1
            return self._db.execute(sql, args)

    def version(self) -> tuple[int, int]:
        """Moves whenever the store is written to: by this process (its own count) or by another one (the
        database's data_version). Cheap, for watching without querying."""
        with self._lock:
            return (self._db.execute("PRAGMA data_version").fetchone()[0], self._writes)

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        """One transaction that holds the write lock from its first read, so
        another process can't change the rows in between."""
        with self._lock:
            self._writes += 1
            if self._db.in_transaction:  # inside one already (a migration step): part of it
                yield self._db
                return
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    # observations

    @staticmethod
    def _obs(r: sqlite3.Row) -> Observation:
        return Observation(
            source=r["source"],
            ts=_dt(r["ts"]),
            kind=r["kind"],
            key=r["key"],
            payload=json.loads(r["payload"]),
            domain=r["domain"],
            goal=r["goal"],
            sensitivity=Sensitivity(r["sensitivity"]),
        )

    def add_observations(self, observations: Iterable[Observation]) -> list[Observation]:
        new = []
        with self._lock:
            self._db.execute("BEGIN")
            try:
                for o in observations:
                    cur = self._db.execute(
                        "INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?,?)",
                        (o.key, o.source, _ts(o.ts), o.kind, o.domain, o.goal, int(o.sensitivity), json.dumps(o.payload, ensure_ascii=False)),
                    )
                    if cur.rowcount:
                        new.append(o)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return new

    def observations(self, since, until=None, *, domain=None, goal=None, kinds=None) -> list[Observation]:
        sql, args = "SELECT * FROM observations WHERE ts >= ?", [_ts(since)]
        if until is not None:
            sql += " AND ts < ?"
            args.append(_ts(until))
        if domain is not None:
            sql += " AND domain = ?"
            args.append(domain)
        if goal is not None:
            sql += " AND goal = ?"
            args.append(goal)
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += list(kinds)
        return [self._obs(r) for r in self._q(sql + " ORDER BY ts", args)]

    def last_activity(self, domain: str) -> datetime | None:
        rows = self._q("SELECT MAX(ts) AS t FROM observations WHERE domain = ?", (domain,))
        return _dt(rows[0]["t"]) if rows and rows[0]["t"] else None

    def unattributed(self, limit: int) -> list[Observation]:
        return [self._obs(r) for r in self._q("SELECT * FROM observations WHERE domain IS NULL ORDER BY ts DESC LIMIT ?", (limit,))]

    def observations_for_repo(self, repo: str) -> list[Observation]:
        """Observations from the repo, and windows whose title names it."""
        rows = self._q(
            "SELECT * FROM observations WHERE json_extract(payload, '$.repo') = ? "
            "OR (kind = 'window' AND lower(json_extract(payload, '$.title')) LIKE ?) ORDER BY ts",
            (repo, f"%{repo.lower()}%"),
        )
        return [self._obs(r) for r in rows]

    def observations_for_window(self, app: str, title: str) -> list[Observation]:
        rows = self._q(
            "SELECT * FROM observations WHERE kind = 'window' AND json_extract(payload, '$.app') = ? "
            "AND substr(json_extract(payload, '$.title'), 1, 120) = ? ORDER BY ts",
            (app, title[:120]),
        )
        return [self._obs(r) for r in rows]

    def reattribute(self, key, domain, goal, sensitivity) -> None:
        self._x("UPDATE observations SET domain=?, goal=?, sensitivity=? WHERE key=?", (domain, goal, int(sensitivity), key))

    # state

    def get_state(self, key: str) -> Any:
        rows = self._q("SELECT value FROM state WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else None

    def set_state(self, key: str, value: Any) -> None:
        self._x("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def update_state(self, key: str, change: Callable[[Any], Any]) -> Any:
        """Reads, changes and writes one key in one transaction that holds the write lock from the read: a write by
        another process (the scheduled runs are processes of their own) cannot land in between and be lost."""
        with self._write() as db:
            rows = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchall()
            value = change(json.loads(rows[0]["value"]) if rows else None)
            db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))
            return value

    def claimed(self, marker: str) -> bool:
        return bool(self._q("SELECT 1 FROM markers WHERE marker=?", (marker,)))

    def claim(self, marker: str, at: datetime) -> bool:
        return self._x("INSERT OR IGNORE INTO markers VALUES (?,?)", (marker, _ts(at))).rowcount == 1

    def release(self, marker: str) -> None:
        self._x("DELETE FROM markers WHERE marker=?", (marker,))

    # signals

    def signal_level(self, key: str, day: date) -> int | None:
        rows = self._q("SELECT level FROM signals WHERE key=? AND day=?", (key, day.isoformat()))
        return rows[0]["level"] if rows else None

    def record_signal(self, key: str, day: date, level: int) -> None:
        self._x(
            "INSERT INTO signals VALUES (?,?,?) ON CONFLICT(key, day) DO UPDATE SET level=MAX(level, excluded.level)",
            (key, day.isoformat(), level),
        )

    # inbox

    @staticmethod
    def _item(r: sqlite3.Row) -> InboxItem:
        return InboxItem(
            id=r["id"], ts=_dt(r["ts"]), domain=r["domain"], kind=r["kind"], title=r["title"], body=r["body"],
            data=json.loads(r["data"]), signal_key=r["signal_key"], level=r["level"], read=bool(r["read"]),
        )

    def add_inbox(self, item: InboxItem) -> InboxItem:
        cur = self._x(
            "INSERT INTO inbox (ts, domain, kind, title, body, data, signal_key, level, read) VALUES (?,?,?,?,?,?,?,?,0)",
            (_ts(item.ts), item.domain, item.kind, item.title, item.body, json.dumps(item.data, ensure_ascii=False), item.signal_key, item.level),
        )
        return InboxItem(cur.lastrowid, item.ts, item.domain, item.kind, item.title, item.body, item.data, item.signal_key, item.level, False)

    def inbox(self, *, limit: int = 100, unread_only: bool = False) -> list[InboxItem]:
        where = "WHERE read = 0" if unread_only else ""
        return [self._item(r) for r in self._q(f"SELECT * FROM inbox {where} ORDER BY id DESC LIMIT ?", (limit,))]

    def mark_read(self, ids: Sequence[int]) -> None:
        if ids:
            self._x(f"UPDATE inbox SET read=1 WHERE id IN ({','.join('?' * len(ids))})", list(ids))

    def mark_unread(self, ids: Sequence[int]) -> None:
        if ids:
            self._x(f"UPDATE inbox SET read=0 WHERE id IN ({','.join('?' * len(ids))})", list(ids))

    # pushes

    def pushes_by_tier(self, day: date) -> dict[str, int]:
        rows = self._q("SELECT tier, COUNT(*) AS n FROM pushes WHERE day=? GROUP BY tier", (day.isoformat(),))
        return {r["tier"]: r["n"] for r in rows}

    def record_push(self, day: date, at: datetime, domain: str, tier: str) -> None:
        self._x("INSERT INTO pushes VALUES (?,?,?,?)", (_ts(at), day.isoformat(), domain, tier))

    # confirmations

    @staticmethod
    def _conf(r: sqlite3.Row) -> Confirmation:
        return Confirmation(
            id=r["id"], ts=_dt(r["ts"]), kind=r["kind"], question=r["question"], options=tuple(json.loads(r["options"])),
            data=json.loads(r["data"]), answer=r["answer"], key=r["key"],
        )

    def add_confirmation(self, c: Confirmation) -> Confirmation | None:
        with self._lock:
            if c.key and self._q("SELECT 1 FROM confirmations WHERE key=? AND answer IS NULL", (c.key,)):
                return None
            cur = self._x(
                "INSERT INTO confirmations (ts, kind, question, options, data, key) VALUES (?,?,?,?,?,?)",
                (_ts(c.ts), c.kind, c.question, json.dumps(list(c.options), ensure_ascii=False), json.dumps(c.data, ensure_ascii=False), c.key),
            )
            return Confirmation(cur.lastrowid, c.ts, c.kind, c.question, c.options, c.data, None, c.key)

    def confirmations(self, *, pending_only: bool = True) -> list[Confirmation]:
        where = "WHERE answer IS NULL" if pending_only else ""
        return [self._conf(r) for r in self._q(f"SELECT * FROM confirmations {where} ORDER BY id DESC")]

    def answer_confirmation(self, cid: int, answer: str, at: datetime) -> Confirmation | None:
        with self._lock:
            rows = self._q("SELECT * FROM confirmations WHERE id=? AND answer IS NULL", (cid,))
            if not rows:
                return None
            c = self._conf(rows[0])
            self._x("UPDATE confirmations SET answer=?, answered_at=? WHERE id=?", (answer, _ts(at), cid))
            return Confirmation(c.id, c.ts, c.kind, c.question, c.options, c.data, answer, c.key)

    # learned rules

    def learned_rules(self) -> dict[str, dict[str, Any]]:
        return {r["key"]: {"domain": r["domain"], "goal": r["goal"]} for r in self._q("SELECT * FROM learned_rules")}

    def shown_files(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in self._q("SELECT t.files, l.project FROM lease_turns t JOIN leases l ON l.lease_id = t.lease_id"
                         " WHERE t.role = 'file' AND t.files IS NOT NULL"):
            for f in json.loads(r["files"] or "[]"):
                if isinstance(f, dict) and f.get("id"):
                    out[str(f["id"])] = str(r["project"])
        return out

    def learn_rule(self, kind: str, match: str, domain: str, goal: str | None) -> None:
        self._x(
            "INSERT INTO learned_rules VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET domain=excluded.domain, goal=excluded.goal",
            (f"{kind}:{match}", domain, goal),
        )

    def forget_repo(self, repo: str) -> int:
        """Drops a repo name that turned out not to be a project: from stored
        payloads, open and answered questions, and learned rules."""
        with self._lock:
            n = self._x(
                "UPDATE observations SET payload = json_remove(payload, '$.repo') WHERE json_extract(payload, '$.repo') = ?",
                (repo,),
            ).rowcount
            self._x("DELETE FROM confirmations WHERE kind = 'attribution' AND json_extract(data, '$.match') = ?", (repo,))
            self._x("DELETE FROM learned_rules WHERE key = ?", (f"repo:{repo}",))
            return n

    def forget_rule(self, kind: str, match: str) -> None:
        self._x("DELETE FROM learned_rules WHERE key=?", (f"{kind}:{match}",))

    # completed

    def completed(self) -> set[str]:
        return {r["id"] for r in self._q("SELECT id FROM completed")}

    def set_completed(self, qualified_id: str, done: bool, at: datetime) -> None:
        if done:
            self._x("INSERT OR IGNORE INTO completed VALUES (?,?)", (qualified_id, _ts(at)))
        else:
            self._x("DELETE FROM completed WHERE id=?", (qualified_id,))

    # subscriptions

    def subscriptions(self) -> list[dict[str, Any]]:
        return [json.loads(r["sub"]) for r in self._q("SELECT sub FROM subscriptions")]

    def add_subscription(self, sub: dict[str, Any], at: datetime) -> None:
        self._x(
            "INSERT INTO subscriptions VALUES (?,?,?) ON CONFLICT(endpoint) DO UPDATE SET sub=excluded.sub, at=excluded.at",
            (sub["endpoint"], json.dumps(sub), _ts(at)),
        )

    def remove_subscription(self, endpoint: str) -> None:
        self._x("DELETE FROM subscriptions WHERE endpoint=?", (endpoint,))

    # status changes

    def record_status_change(self, domain, target, before, after, at) -> int:
        cur = self._x(
            "INSERT INTO status_changes (domain, target, before, after, at) VALUES (?,?,?,?,?)",
            (domain, target, json.dumps(before), json.dumps(after), _ts(at)),
        )
        return int(cur.lastrowid)

    def status_change(self, change_id: int) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM status_changes WHERE id=?", (change_id,))
        if not rows:
            return None
        r = rows[0]
        return {"id": r["id"], "domain": r["domain"], "target": r["target"], "before": json.loads(r["before"]), "after": json.loads(r["after"])}

    # prepared items

    @staticmethod
    def _prep(r: sqlite3.Row) -> Prepared:
        return Prepared(
            id=r["id"], ts=_dt(r["ts"]), domain=r["domain"], target=r["target"], kind=r["kind"], title=r["title"],
            data=json.loads(r["data"]), status=r["status"], prepared_by=r["prepared_by"],
        )

    def add_prepared(self, p: Prepared) -> Prepared:
        cur = self._x(
            "INSERT INTO prepared (ts, domain, target, kind, title, data, status, prepared_by) VALUES (?,?,?,?,?,?,?,?)",
            (_ts(p.ts), p.domain, p.target, p.kind, p.title, json.dumps(p.data, ensure_ascii=False), p.status, p.prepared_by),
        )
        return Prepared(cur.lastrowid, p.ts, p.domain, p.target, p.kind, p.title, p.data, p.status, p.prepared_by)

    def prepared(self, *, status: str | None = "ready") -> list[Prepared]:
        if status is None:
            rows = self._q("SELECT * FROM prepared ORDER BY id DESC")
        else:
            rows = self._q("SELECT * FROM prepared WHERE status=? ORDER BY id DESC", (status,))
        return [self._prep(r) for r in rows]

    def get_prepared(self, pid: int) -> Prepared | None:
        rows = self._q("SELECT * FROM prepared WHERE id=?", (pid,))
        return self._prep(rows[0]) if rows else None

    def set_prepared_status(self, pid: int, status: str) -> None:
        self._x("UPDATE prepared SET status=? WHERE id=?", (status, pid))

    # actions taken on the user's behalf

    @staticmethod
    def _act(r: sqlite3.Row) -> ActionRecord:
        return ActionRecord(r["id"], _dt(r["ts"]), r["domain"], r["text"], json.loads(r["undo"]), bool(r["undone"]))

    def log_action(self, a: ActionRecord) -> ActionRecord:
        cur = self._x(
            "INSERT INTO actions (ts, domain, text, undo, undone) VALUES (?,?,?,?,0)",
            (_ts(a.ts), a.domain, a.text, json.dumps(a.undo, ensure_ascii=False)),
        )
        return ActionRecord(cur.lastrowid, a.ts, a.domain, a.text, a.undo, False)

    def actions(self, since: datetime) -> list[ActionRecord]:
        return [self._act(r) for r in self._q("SELECT * FROM actions WHERE ts >= ? ORDER BY id DESC", (_ts(since),))]

    def get_action(self, aid: int) -> ActionRecord | None:
        rows = self._q("SELECT * FROM actions WHERE id=?", (aid,))
        return self._act(rows[0]) if rows else None

    def mark_action_undone(self, aid: int) -> None:
        self._x("UPDATE actions SET undone=1 WHERE id=?", (aid,))

    # conversations and digests

    def add_turns(self, turns: Iterable[Turn]) -> int:
        n = 0
        with self._lock:
            self._db.execute("BEGIN")
            try:
                for t in turns:
                    n += self._db.execute(
                        "INSERT OR IGNORE INTO turns VALUES (?,?,?,?,?,?)",
                        (t.key, t.session, _ts(t.ts), t.role, t.project, t.text),
                    ).rowcount
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return n

    def sessions_to_digest(self, limit: int) -> list[dict[str, Any]]:
        rows = self._q(
            """
            SELECT t.session, MAX(t.project) AS project, d.last AS after, MAX(t.ts) AS until
            FROM turns t
            LEFT JOIN (SELECT session, MAX(until) AS last FROM digests GROUP BY session) d ON d.session = t.session
            WHERE t.role = 'user' AND (d.last IS NULL OR t.ts > d.last)
            GROUP BY t.session
            ORDER BY until DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            {"session": r["session"], "project": r["project"], "after": _dt(r["after"]) if r["after"] else None, "until": _dt(r["until"])}
            for r in rows
        ]

    def turns(self, session: str, after: datetime | None, until: datetime) -> list[Turn]:
        rows = self._q(
            "SELECT * FROM turns WHERE session = ? AND ts > ? AND ts <= ? ORDER BY ts",
            (session, _ts(after) if after else "", _ts(until)),
        )
        return [Turn(r["key"], r["session"], _dt(r["ts"]), r["role"], r["project"], r["text"]) for r in rows]

    def save_digest(self, session: str, project: str | None, until: datetime, data: dict[str, Any], at: datetime) -> None:
        self._x(
            "INSERT OR REPLACE INTO digests VALUES (?,?,?,?,?)",
            (session, _ts(until), project, json.dumps(data, ensure_ascii=False), _ts(at)),
        )

    def digests(self, since: datetime, until: datetime) -> list[dict[str, Any]]:
        rows = self._q("SELECT * FROM digests WHERE until >= ? AND until < ? ORDER BY until", (_ts(since), _ts(until)))
        return [{"session": r["session"], "project": r["project"], "until": _dt(r["until"]), **json.loads(r["data"])} for r in rows]

    # evaluation cases: what the models were asked, what they said, what the user corrected

    def add_eval_case(self, ts: datetime, task: str, system: str, data: dict[str, Any], reply: str | None,
                      backend: str | None, *, by_local: bool, cloud_ok: bool, json_mode: bool, ms: int | None = None,
                      tokens: tuple[int | None, int | None] = (None, None)) -> int:
        cur = self._x(
            "INSERT INTO eval_cases (ts, task, system, data, reply, backend, by_local, cloud_ok, json_mode, ms, input_tokens, output_tokens)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (_ts(ts), task, system, json.dumps(data, ensure_ascii=False, default=str), None if reply is None else str(reply), backend,
             int(by_local), int(cloud_ok), int(json_mode), ms, *tokens),
        )
        return int(cur.lastrowid)

    def add_usage(self, ts: datetime, backend: str, input_tokens: int, output_tokens: int) -> None:
        self._x("INSERT INTO usage VALUES (?,?,?,?)", (_ts(ts), backend, input_tokens, output_tokens))

    def usage_since(self, since: datetime) -> list[tuple[str | None, int, int]]:
        rows = self._q("SELECT backend, SUM(input_tokens) AS i, SUM(output_tokens) AS o FROM usage WHERE ts >= ? GROUP BY backend",
                       (_ts(since),))
        return [(r["backend"], int(r["i"]), int(r["o"])) for r in rows]

    def _usage_from_cases(self) -> None:
        """Before usage was kept apart, a request's tokens were kept with its case only: the last two days of those
        are what a daily limit still counts, so they come along once."""
        if self._db.execute("SELECT 1 FROM usage LIMIT 1").fetchall():
            return
        self._db.execute("INSERT INTO usage SELECT ts, backend, COALESCE(input_tokens, 0), COALESCE(output_tokens, 0) FROM eval_cases"
                         " WHERE backend IS NOT NULL AND (input_tokens IS NOT NULL OR output_tokens IS NOT NULL) AND ts >= ?",
                         (_ts(datetime.now(timezone.utc) - timedelta(days=2)),))

    def set_case_verdict(self, case_id: int, verdict: dict[str, Any]) -> None:
        self._x("UPDATE eval_cases SET verdict = ? WHERE id = ?", (json.dumps(verdict, ensure_ascii=False), case_id))

    def add_case_feedback(self, case_id: int, feedback: dict[str, Any]) -> None:
        with self._lock:
            rows = self._q("SELECT feedback FROM eval_cases WHERE id = ?", (case_id,))
            if not rows:
                return
            items = json.loads(rows[0]["feedback"] or "[]") + [feedback]
            self._x("UPDATE eval_cases SET feedback = ? WHERE id = ?", (json.dumps(items, ensure_ascii=False), case_id))

    def eval_cases(self, task: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        where, args = ("WHERE task = ?", (task,)) if task else ("", ())
        rows = self._q(f"SELECT * FROM eval_cases {where} ORDER BY id DESC LIMIT ?", (*args, limit))
        return [{"id": r["id"], "ts": _dt(r["ts"]), "task": r["task"], "system": r["system"], "data": json.loads(r["data"]),
                 "reply": r["reply"], "backend": r["backend"], "by_local": bool(r["by_local"]), "cloud_ok": bool(r["cloud_ok"]), "json_mode": bool(r["json_mode"]),
                 "feedback": json.loads(r["feedback"] or "[]"), "ms": r["ms"], "tokens": (r["input_tokens"], r["output_tokens"]),
                 "verdict": json.loads(r["verdict"]) if r["verdict"] else None} for r in rows]

    def get_inbox(self, item_id: int) -> InboxItem | None:
        rows = self._q("SELECT * FROM inbox WHERE id = ?", (item_id,))
        return self._item(rows[0]) if rows else None

    # handoffs: facts passed on to the projects they concern

    HANDOFF_COLUMNS = ("id", "ts", "status")

    @staticmethod
    def _handoff(r: sqlite3.Row) -> dict[str, Any]:
        return {"id": r["id"], "ts": r["ts"], "status": r["status"], **json.loads(r["data"])}

    def add_handoff(self, fields: dict[str, Any], at: datetime, *,
                    replaces: Callable[[dict[str, Any], dict[str, Any]], bool]) -> dict[str, Any]:
        data = {k: v for k, v in fields.items() if k not in self.HANDOFF_COLUMNS}
        project, item, fact = data.get("project"), data.get("item"), data.get("fact")
        with self._write() as db:
            same = db.execute(
                "SELECT * FROM handoffs WHERE status = 'pending' AND project IS ? AND item IS ? AND (item IS NOT NULL OR fact IS ?)"
                " AND json_extract(data, '$.reply_to') IS ? ORDER BY id LIMIT 1",
                (project, item, fact, data.get("reply_to"))).fetchone()
            if same:
                old = self._handoff(same)
                if not replaces(data, old):
                    return old
                merged = {**{k: v for k, v in old.items() if k not in self.HANDOFF_COLUMNS}, **data}
                db.execute("UPDATE handoffs SET project = ?, item = ?, fact = ?, data = ?, changed = ? WHERE id = ?",
                           (project, item, fact, json.dumps(merged, ensure_ascii=False), _ts(at), same["id"]))
                return {"id": same["id"], "ts": same["ts"], "status": same["status"], **merged}
            # a later handoff is told later: its time is after the last one's by at least two microseconds (the records it
            # stands for take one each), whichever process adds it (core section 4: the principal side's times only rise)
            last = db.execute("SELECT ts FROM handoffs ORDER BY id DESC LIMIT 1").fetchone()
            if last is not None and (before := _parse_ts(last["ts"])) is not None and at <= before + timedelta(microseconds=1):
                at = before + timedelta(microseconds=2)
            cur = db.execute(
                "INSERT INTO handoffs (ts, status, project, item, fact, data, changed) VALUES (?, 'pending', ?, ?, ?, ?, ?)",
                (at.isoformat(), project, item, fact, json.dumps(data, ensure_ascii=False), _ts(at)))
            return {"id": int(cur.lastrowid), "ts": at.isoformat(), "status": "pending", **data}

    def handoffs(self, *, status: str | None = "pending", since: int = 0) -> list[dict[str, Any]]:
        where, args = ("AND status = ?", (status,)) if status is not None else ("", ())
        return [self._handoff(r) for r in self._q(f"SELECT * FROM handoffs WHERE id > ? {where} ORDER BY id", (since, *args))]

    # Principal Handoff records the projects sent

    def add_record(self, record: dict[str, Any], at: datetime, canonical: Callable[[dict[str, Any]], str]) -> str:
        key = (record["ext"], record["project"], record["origin"], record["id"])  # an id is unique per origin (core section 10)
        text = json.dumps(record, ensure_ascii=False)
        with self._write() as db:
            old = db.execute("SELECT record FROM ph_records WHERE ext = ? AND project = ? AND origin = ? AND id = ?", key).fetchone()
            if old is None:
                db.execute("INSERT INTO ph_records (ext, project, origin, id, key, record, received) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (*key, record["key"], text, _ts(at)))
                return "new"
            if canonical(record) > canonical(json.loads(old["record"])):  # an id reused: the greater form, whatever came first
                db.execute("UPDATE ph_records SET record = ? WHERE ext = ? AND project = ? AND origin = ? AND id = ?", (text, *key))
                return "replaced"
            return "duplicate"

    def _records_as_04(self) -> None:
        """Records kept under Principal Handoff 0.3 read as 0.4 (acts section 8: only `ph` and `ext` change), so one sent
        again is the same record; and a record's id is unique per origin from now on (core section 10)."""
        sql = (self._db.execute("SELECT sql FROM sqlite_master WHERE name = 'ph_records'").fetchone() or [""])[0]
        if "origin" not in sql:
            self._db.execute("ALTER TABLE ph_records RENAME TO ph_records_03")
            for statement in _statements(SCHEMA):
                if "ph_records" in statement:
                    self._db.execute(statement)
            self._db.execute("INSERT INTO ph_records (seq, ext, project, origin, id, key, record, received) SELECT seq, ext, project,"
                             " COALESCE(json_extract(record, '$.origin'), ''), id, key, record, received FROM ph_records_03")
            self._db.execute("DROP TABLE ph_records_03")
        # what each project sent last is what it speaks, until it sends again (so it is answered as it reads)
        latest = self._db.execute("SELECT project, json_extract(record, '$.ph') AS ph FROM ph_records r WHERE seq ="
                                  " (SELECT MAX(seq) FROM ph_records WHERE project = r.project)").fetchall()
        if latest and not self._db.execute("SELECT 1 FROM state WHERE key = ?", (SPEAKS,)).fetchall():
            self._db.execute("INSERT INTO state VALUES (?, ?)", (SPEAKS, json.dumps({x["project"]: x["ph"] for x in latest})))
        for old, new in OLDER_EXTS.items():
            self._db.execute("UPDATE ph_records SET ext = ?, record = json_set(record, '$.ph', ?, '$.ext', ?) WHERE ext = ?",
                             (new, PH, new, old))

    def records_of(self, ext: str, project: str, key: str) -> list[dict[str, Any]]:
        return [json.loads(r["record"]) for r in self._q("SELECT record FROM ph_records WHERE ext = ? AND project = ? AND key = ? ORDER BY seq",
                                                          (ext, project, key))]

    def handoffs_to(self, project: str, since: int = 0, key: str | None = None) -> list[dict[str, Any]]:
        where, args = ("AND json_extract(data, '$.reply_to') = ?", (key,)) if key is not None else ("", ())
        return [self._handoff(r) for r in self._q(f"SELECT * FROM handoffs WHERE project = ? AND id > ? {where} ORDER BY id",
                                                  (project, since, *args))]

    def rename_handoff_project(self, old: str, new: str) -> None:
        with self._write() as db:
            for r in db.execute("SELECT id, data FROM handoffs WHERE project = ?", (old,)).fetchall():
                data = {**json.loads(r["data"]), "project": new}
                db.execute("UPDATE handoffs SET project = ?, data = ? WHERE id = ?", (new, json.dumps(data, ensure_ascii=False), r["id"]))

    def handoff(self, hid: int) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM handoffs WHERE id = ?", (hid,))
        return self._handoff(rows[0]) if rows else None

    def set_handoff_status(self, hid: int, status: str, at: datetime) -> dict[str, Any] | None:
        with self._write() as db:
            if not db.execute("UPDATE handoffs SET status = ?, changed = ? WHERE id = ?", (status, _ts(at), hid)).rowcount:
                return None
            return self._handoff(db.execute("SELECT * FROM handoffs WHERE id = ?", (hid,)).fetchone())

    def prune_handoffs(self, before: datetime) -> int:
        return self._x("DELETE FROM handoffs WHERE status != 'pending' AND changed < ?", (_ts(before),)).rowcount

    # project messages: questions, progress and done reports from the projects

    @staticmethod
    def _project_message(r: sqlite3.Row) -> dict[str, Any]:
        return {k: r[k] for k in ("id", "ts", "project", "type", "text", "handoff_id", "status", "answer", "context")}

    def add_project_message(self, m: dict[str, Any], at: datetime) -> bool:
        status = "open" if m["type"] == "question" else "received"
        return bool(self._x(
            "INSERT OR IGNORE INTO project_messages (id, ts, project, type, text, handoff_id, status, changed, context)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (m["id"], at.isoformat(), m["project"], m["type"], m["text"], m.get("handoff_id"), status, _ts(at),
             m.get("context"))).rowcount)

    def project_messages(self, *, type: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        rows = self._q("SELECT * FROM project_messages WHERE (? IS NULL OR type = ?) AND (? IS NULL OR status = ?)"
                       " ORDER BY ts, id", (type, type, status, status))
        return [self._project_message(r) for r in rows]

    def answer_project_message(self, mid: str, answer: str, at: datetime) -> dict[str, Any] | None:
        with self._write() as db:
            if not db.execute("UPDATE project_messages SET status = 'answered', answer = ?, changed = ?"
                              " WHERE id = ? AND type = 'question' AND status = 'open'", (answer, _ts(at), mid)).rowcount:
                return None
            return self._project_message(db.execute("SELECT * FROM project_messages WHERE id = ?", (mid,)).fetchone())

    def settle_project_message(self, mid: str, how: str, at: datetime) -> dict[str, Any] | None:
        with self._write() as db:
            if not db.execute("UPDATE project_messages SET status = 'settled', answer = ?, changed = ?"
                              " WHERE id = ? AND type = 'question' AND status = 'open'", (how, _ts(at), mid)).rowcount:
                return None
            return self._project_message(db.execute("SELECT * FROM project_messages WHERE id = ?", (mid,)).fetchone())

    def amend_project_message(self, mid: str, text: str, at: datetime) -> bool:
        return bool(self._x("UPDATE project_messages SET text = ?, changed = ? WHERE id = ? AND type = 'question' AND status = 'open'",
                            (text, _ts(at), mid)).rowcount)

    def dismiss_project_message(self, mid: str, at: datetime) -> dict[str, Any] | None:
        with self._write() as db:
            if not db.execute("UPDATE project_messages SET status = 'dismissed', changed = ?"
                              " WHERE id = ? AND type = 'question' AND status = 'open'", (_ts(at), mid)).rowcount:
                return None
            return self._project_message(db.execute("SELECT * FROM project_messages WHERE id = ?", (mid,)).fetchone())

    # tasks: what only the user can do, one row each; origins are the projects that asked

    def _task(self, db: sqlite3.Connection, r: sqlite3.Row | None) -> dict[str, Any] | None:
        if r is None:
            return None
        origins = [{"project": o["project"], "ref": o["ref"], "text": o["text"]}
                   for o in db.execute("SELECT * FROM task_origins WHERE key = ? ORDER BY rowid", (r["key"],))]
        return {"key": r["key"], "n": r["n"], "title": r["title"], "due": r["due"], "status": r["status"],
                "context": r["context"], "links": json.loads(r["links"]), "created": r["created"], "closed": r["closed"],
                "closed_by": r["closed_by"], "why": r["why"], "origins": origins}

    def add_task(self, key: str, fields: dict[str, Any], at: datetime) -> tuple[dict[str, Any], bool]:
        with self._write() as db:
            row = db.execute("SELECT * FROM tasks WHERE key = ?", (key,)).fetchone()
            if row is not None:
                return self._task(db, row), False
            n = (db.execute("SELECT MAX(n) FROM tasks").fetchone()[0] or 0) + 1
            status = fields.get("status", "open")
            db.execute("INSERT INTO tasks (key, n, title, due, status, context, links, created, closed, closed_by, why)"
                       " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (key, n, fields["title"], fields.get("due"), status, fields.get("context"),
                        json.dumps(fields.get("links") or [], ensure_ascii=False), _ts(at),
                        _ts(at) if status != "open" else None, fields.get("closed_by"), fields.get("why")))
            return self._task(db, db.execute("SELECT * FROM tasks WHERE key = ?", (key,)).fetchone()), True

    def update_task(self, key: str, at: datetime, **fields: Any) -> dict[str, Any] | None:
        cols = {k: (json.dumps(v, ensure_ascii=False) if k == "links" else v) for k, v in fields.items()
                if k in ("title", "due", "status", "context", "links", "closed_by", "why")}
        if "status" in cols:
            cols["closed"] = None if cols["status"] == "open" else _ts(at)
        with self._write() as db:
            if cols:
                db.execute(f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in cols)} WHERE key = ?", (*cols.values(), key))
            return self._task(db, db.execute("SELECT * FROM tasks WHERE key = ?", (key,)).fetchone())

    def task(self, key: str) -> dict[str, Any] | None:
        """By its key, by an origin message id it took in, or by its short number."""
        with self._lock:
            db = self._db
            row = db.execute("SELECT * FROM tasks WHERE key = ?", (key,)).fetchone()
            if row is None:
                o = db.execute("SELECT key FROM task_origins WHERE ref = ?", (key,)).fetchone()
                if o is not None:
                    row = db.execute("SELECT * FROM tasks WHERE key = ?", (o["key"],)).fetchone()
            if row is None and key.upper().startswith("T-") and key[2:].isdigit():
                row = db.execute("SELECT * FROM tasks WHERE n = ?", (int(key[2:]),)).fetchone()
            return self._task(db, row)

    def tasks(self, *, status: str | None = "open", project: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tasks WHERE (? IS NULL OR status = ?) AND (? IS NULL OR key IN"
                " (SELECT key FROM task_origins WHERE project = ?)) ORDER BY due IS NULL, due, n",
                (status, status, project, project)).fetchall()
            return [self._task(self._db, r) for r in rows]

    def add_task_origin(self, key: str, project: str, ref: str, text: str | None) -> None:
        self._x("INSERT OR IGNORE INTO task_origins (key, project, ref, text) VALUES (?,?,?,?)", (key, project, ref, text))

    def delete_task(self, key: str) -> None:
        with self._write() as db:
            db.execute("DELETE FROM task_origins WHERE key = ?", (key,))
            db.execute("DELETE FROM tasks WHERE key = ?", (key,))

    def remove_task_origin(self, key: str, ref: str) -> None:
        self._x("DELETE FROM task_origins WHERE key = ? AND ref = ?", (key, ref))

    # leases: conversation threads lent to a project's session

    LEASE_FIELDS = ("thread", "project", "mode", "state", "started", "ended", "last_seq", "last_activity",
                    "last_user_at", "warn_state", "warned_at", "handover", "end_reason", "error", "answering", "left_at",
                    "end_asked")

    @staticmethod
    def _lease(r: sqlite3.Row | None) -> dict[str, Any] | None:
        if r is None:
            return None
        d = {k: r[k] for k in r.keys()}
        d["handover"] = json.loads(d["handover"]) if d["handover"] else None
        return d

    def add_lease(self, lease: dict[str, Any]) -> None:
        cols = ["lease_id", *(k for k in self.LEASE_FIELDS if k in lease)]
        vals = [json.dumps(lease[k], ensure_ascii=False) if k == "handover" and lease[k] is not None else lease[k]
                for k in cols]
        self._x(f"INSERT OR IGNORE INTO leases ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)

    def lease(self, lease_id: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM leases WHERE lease_id = ?", (lease_id,))
        return self._lease(rows[0]) if rows else None

    def leases(self, *, active: bool = True) -> list[dict[str, Any]]:
        where = "WHERE state NOT IN ('detached')" if active else ""
        return [self._lease(r) for r in self._q(f"SELECT * FROM leases {where} ORDER BY started DESC")]

    def update_lease(self, lease_id: str, **fields: Any) -> dict[str, Any] | None:
        cols = {k: (json.dumps(v, ensure_ascii=False) if k == "handover" and v is not None else v)
                for k, v in fields.items() if k in self.LEASE_FIELDS}
        with self._write() as db:
            if cols:
                db.execute(f"UPDATE leases SET {', '.join(f'{k} = ?' for k in cols)} WHERE lease_id = ?",
                           (*cols.values(), lease_id))
            return self._lease(db.execute("SELECT * FROM leases WHERE lease_id = ?", (lease_id,)).fetchone())

    def add_lease_turn(self, lease_id: str, role: str, text: str, at: datetime, files: Sequence[dict[str, str]] = (),
                       detail: str = "") -> None:
        self._x("INSERT INTO lease_turns (lease_id, role, text, ts, files, detail) VALUES (?, ?, ?, ?, ?, ?)",
                (lease_id, role, text, at.isoformat(), json.dumps(list(files), ensure_ascii=False) if files else None, detail or None))

    def lease_turns(self, lease_id: str) -> list[dict[str, Any]]:
        return [{"role": r["role"], "text": r["text"], "ts": r["ts"], **({"files": json.loads(r["files"])} if r["files"] else {}),
                 **({"detail": r["detail"]} if r["detail"] else {})}
                for r in self._q("SELECT * FROM lease_turns WHERE lease_id = ? ORDER BY id", (lease_id,))]

    # permission requests from a lent session's relay

    @staticmethod
    def _permission(r: sqlite3.Row | None) -> dict[str, Any] | None:
        if r is None:
            return None
        d = {k: r[k] for k in r.keys()}
        d["answers"] = json.loads(d["answers"]) if d["answers"] else None
        d["question"] = json.loads(d["question"]) if d["question"] else None
        return d

    def _kinds_for_old_requests(self) -> None:
        """Requests kept before they said what they are about (`kind`): said once, from the agent's tool name."""
        with self._write() as db:
            db.execute("UPDATE permission_requests SET kind = CASE WHEN risk IN ('write_file', 'run_command') THEN risk"
                       " WHEN tool = 'AskUserQuestion' THEN 'ask_user' WHEN tool = 'WebFetch' THEN 'open_web' ELSE 'other' END"
                       " WHERE kind IS NULL")

    def add_permission_request(self, req: dict[str, Any], at: datetime) -> dict[str, Any]:
        with self._write() as db:
            db.execute("INSERT OR IGNORE INTO permission_requests (id, lease_id, project, tool, kind, input_summary, summary,"
                       " risk, question, created, v) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (req["id"], req.get("lease"), req.get("project"), req.get("tool") or req["kind"], req["kind"],
                        req.get("target") or req.get("input_summary") or req.get("summary"), req.get("summary"), req.get("risk"),
                        json.dumps(req["question"], ensure_ascii=False) if req.get("question") is not None else None,
                        at.isoformat(), int(req.get("v") or 1)))
            return self._permission(db.execute("SELECT * FROM permission_requests WHERE id = ?", (req["id"],)).fetchone())

    def permission_request(self, rid: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM permission_requests WHERE id = ?", (rid,))
        return self._permission(rows[0]) if rows else None

    def permission_requests(self, *, pending: bool = True) -> list[dict[str, Any]]:
        where = "WHERE decision IS NULL" if pending else ""
        return [self._permission(r) for r in self._q(f"SELECT * FROM permission_requests {where} ORDER BY created")]

    def decide_permission(self, rid: str, decision: str, answers: dict[str, Any] | None, by: str,
                          at: datetime) -> dict[str, Any] | None:
        with self._write() as db:
            db.execute("UPDATE permission_requests SET decision = ?, answers = ?, by = ?, decided = ?"
                       " WHERE id = ? AND decision IS NULL",
                       (decision, json.dumps(answers, ensure_ascii=False) if answers else None, by, at.isoformat(), rid))
            return self._permission(db.execute("SELECT * FROM permission_requests WHERE id = ?", (rid,)).fetchone())

    def _add_column(self, table: str, column: str, decl: str) -> None:
        """A column added after the table first shipped (CREATE TABLE IF NOT EXISTS leaves old tables as they were)."""
        with self._write() as db:
            if column not in {r["name"] for r in db.execute(f"PRAGMA table_info({table})")}:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    def _handover_cards(self) -> None:
        """The turns a finished deep talk added before they kept what its handover said, for the conversation to
        draw: each given the card read back from its own text (a talk resumed and given back again has several,
        and only its own text says which handover each was). A card that already matches its text stays, so
        this changes nothing the second time."""
        from muninn.core.lease import card_from_text, handover_text
        from muninn.languages import language

        said_in = language("zh-TW")  # the turns kept before cards were said in Chinese: the only language Muninn spoke then
        with self._write() as db:
            for lease in db.execute("SELECT DISTINCT lease_id, project FROM leases WHERE ended IS NOT NULL").fetchall():
                via = f"lease:{lease['lease_id']}"
                for row in db.execute("SELECT thread, ix, data FROM thread_turns WHERE data LIKE ?", (f'%"via": "{via}"%',)).fetchall():
                    turn = json.loads(row["data"])
                    text = str(turn.get("text") or "")
                    if turn.get("via") != via:
                        continue
                    kept = turn.get("handover")
                    if kept and handover_text(lease["project"], kept if not kept.get("fallback") else None, words=said_in) == text:
                        continue  # written with its card: it says what the text says, with the reasons too
                    card = card_from_text(lease["project"], text)
                    if card is None:
                        continue
                    turn["handover"] = card
                    db.execute("UPDATE thread_turns SET data = ? WHERE thread = ? AND ix = ?",
                               (json.dumps(turn, ensure_ascii=False, default=str), row["thread"], row["ix"]))

    def _migrate_threads(self) -> None:
        """The conversations used to be one JSON blob in `state` (the latest fifty); moved into rows
        once, keeping their ids. The blob stays where it was."""
        with self._write() as db:
            row = db.execute("SELECT value FROM state WHERE key = 'threads'").fetchone()
            if row is None or db.execute("SELECT 1 FROM threads LIMIT 1").fetchone():
                return
        for tid, t in (json.loads(row["value"]) or {}).items():
            self.save_thread(tid, t)

    # ── conversations ──

    def thread(self, tid: str) -> dict[str, Any] | None:
        rows = self._q("SELECT * FROM threads WHERE id = ?", (tid,))
        return self._thread(rows[0]) if rows else None

    def _thread(self, r: sqlite3.Row) -> dict[str, Any]:
        t = json.loads(r["data"])
        t["turns"] = [json.loads(x["data"]) for x in self._q("SELECT data FROM thread_turns WHERE thread = ? ORDER BY ix", (r["id"],))]
        return t

    def save_thread(self, tid: str, t: dict[str, Any]) -> None:
        turns = list(t.get("turns") or [])
        fields = {k: v for k, v in t.items() if k != "turns"}
        about = t.get("about") or {}
        with self._write() as db:
            db.execute("INSERT INTO threads (id, ts, about_kind, about_ref, data) VALUES (?,?,?,?,?) "
                       "ON CONFLICT(id) DO UPDATE SET ts = excluded.ts, about_kind = excluded.about_kind, "
                       "about_ref = excluded.about_ref, data = excluded.data",
                       (tid, str(t.get("ts") or ""), about.get("kind"), str(about["id"]) if about.get("id") is not None else None,
                        json.dumps(fields, ensure_ascii=False, default=str)))
            # what is stored is exactly what is given: turns added at the end are added, a turn changed in place (or
            # moved by one put before it) is written again, and what is no longer given goes
            stored = dict(db.execute("SELECT ix, data FROM thread_turns WHERE thread = ?", (tid,)).fetchall())
            given = [json.dumps(x, ensure_ascii=False, default=str) for x in turns]
            db.execute("DELETE FROM thread_turns WHERE thread = ? AND ix >= ?", (tid, len(given)))
            db.executemany("UPDATE thread_turns SET data = ? WHERE thread = ? AND ix = ?",
                           [(d, tid, i) for i, d in enumerate(given) if i in stored and stored[i] != d])
            db.executemany("INSERT INTO thread_turns (thread, ix, data) VALUES (?,?,?)",
                           [(tid, i, d) for i, d in enumerate(given) if i not in stored])

    def update_thread(self, tid: str, change: Callable[[dict[str, Any] | None], dict[str, Any] | None]) -> dict[str, Any] | None:
        """Reads a thread (None: there is none), changes it and keeps what `change` returns (None: nothing), in one
        transaction: a turn another process added meanwhile is in what `change` is given, never dropped."""
        with self._write():
            t = change(self.thread(tid))
            if t is not None:
                self.save_thread(tid, t)
            return t

    def threads(self, limit: int, before: str | None = None) -> list[tuple[str, dict[str, Any]]]:
        where, args = ("WHERE ts < ?", (before,)) if before else ("", ())
        return [(r["id"], self._thread(r)) for r in self._q(f"SELECT * FROM threads {where} ORDER BY ts DESC LIMIT ?", (*args, limit))]

    def thread_stamps(self, limit: int) -> list[tuple[str, str, int]]:
        return [(r["id"], r["ts"], r["n"]) for r in self._q(
            "SELECT t.id, t.ts, (SELECT COUNT(*) FROM thread_turns WHERE thread = t.id) AS n FROM threads t ORDER BY t.ts DESC LIMIT ?",
            (limit,))]

    def threads_about(self, kind: str, ref: str) -> list[tuple[str, dict[str, Any]]]:
        return [(r["id"], self._thread(r))
                for r in self._q("SELECT * FROM threads WHERE about_kind = ? AND about_ref = ? ORDER BY ts DESC", (kind, str(ref)))]

    def _migrate_handoffs(self) -> None:
        """Handoffs used to be one JSON list in `state`; moved into rows once,
        keeping their ids."""
        with self._write() as db:
            row = db.execute("SELECT value FROM state WHERE key = 'handoffs'").fetchone()
            if row is None:
                return
            for h in json.loads(row["value"]) or []:
                data = {k: v for k, v in h.items() if k not in self.HANDOFF_COLUMNS}
                db.execute("INSERT OR IGNORE INTO handoffs (id, ts, status, project, item, fact, data, changed) VALUES (?,?,?,?,?,?,?,?)",
                           (h["id"], h["ts"], h["status"], data.get("project"), data.get("item"), data.get("fact"),
                            json.dumps(data, ensure_ascii=False), _ts(_dt(h["ts"]))))
            db.execute("DELETE FROM state WHERE key = 'handoffs'")
