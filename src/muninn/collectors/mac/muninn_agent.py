#!/usr/bin/env python3
"""Muninn's laptop agent. Runs every few minutes from launchd and ships:

- window activity from ActivityWatch, only while the user is not AFK. A
  Chrome window that overlaps an event from the browser extension (installed
  only in the work profile) is marked browser_profile=work;
- commit metadata from the laptop's repositories, keyed exactly like the
  server's own git source so the same commit is never counted twice;
- the work Claude Code transcripts (rsync to the server, read there).

Standard library only, so it runs on the stock macOS python3.
Config: ~/.muninn-agent/config.json (written by install.sh). What it sends: contracts/activity.v1.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOME = Path.home()
AGENT_DIR = HOME / ".muninn-agent"
CONFIG = AGENT_DIR / "config.json"
STATE = AGENT_DIR / "state.json"
AW = "http://127.0.0.1:5600/api/0"
LOOKBACK = timedelta(days=14)
BATCH = 500
GIT_SCAN_VERSION = 2  # bump when repo discovery changes, to rescan the lookback window

LANGS = {".go": "go", ".py": "python", ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
         ".jsx": "javascript", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".swift": "swift",
         ".rb": "ruby", ".sql": "sql", ".sh": "shell", ".c": "c", ".h": "c", ".cpp": "cpp", ".tex": "latex"}
AI_COAUTHOR = ("noreply@anthropic.com", "claude", "copilot", "openai", "codex", "cursor")


def log(msg: str) -> None:
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ── ActivityWatch ───────────────────────────────────────────────────────


def aw_get(path: str):
    with urllib.request.urlopen(f"{AW}{path}", timeout=20) as r:
        return json.load(r)


def aw_query(query: list[str], start: datetime, end: datetime) -> list[dict]:
    body = json.dumps({"query": query, "timeperiods": [f"{iso(start)}/{iso(end)}"]}).encode()
    req = urllib.request.Request(f"{AW}/query/", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)[0]


def find_bucket(buckets: dict, prefix: str) -> str | None:
    names = sorted(b for b in buckets if b.startswith(prefix))
    return names[0] if names else None


def overlaps(ev: dict, spans: list[tuple[datetime, datetime]]) -> bool:
    s = parse_ts(ev["timestamp"])
    e = s + timedelta(seconds=float(ev.get("duration", 0)))
    return any(a < e and s < b for a, b in spans)


def window_observations(events: list[dict], work_spans: list[tuple[datetime, datetime]], browsers: set[str]) -> list[dict]:
    out = []
    for ev in events:
        data = ev.get("data") or {}
        app, title = str(data.get("app", "")), str(data.get("title", ""))
        dur = float(ev.get("duration", 0))
        if dur < 5:
            continue  # flicker while switching windows
        payload = {"app": app, "title": title, "duration_s": round(dur)}
        if app in browsers:
            payload["browser_profile"] = "work" if overlaps(ev, work_spans) else "personal"
        key = hashlib.sha1(f"{ev['timestamp']}|{app}|{title}".encode()).hexdigest()[:20]
        out.append({"source": "aw", "key": key, "ts": ev["timestamp"], "kind": "window", "payload": payload,
                    "sensitivity": "personal"})
    return out


def collect_windows(cfg: dict, state: dict, now: datetime) -> list[dict]:
    try:
        buckets = aw_get("/buckets/")
    except (urllib.error.URLError, OSError) as e:
        log(f"activitywatch not reachable: {e}")
        return []
    win = find_bucket(buckets, "aw-watcher-window")
    afk = find_bucket(buckets, "aw-watcher-afk")
    if not win or not afk:
        log("activitywatch buckets missing (window/afk watcher not running?)")
        return []
    start = parse_ts(state["aw_until"]) if state.get("aw_until") else now - LOOKBACK
    end = now - timedelta(minutes=2)  # the current event is still growing
    if end <= start:
        return []
    events = aw_query([
        f'afk = flood(query_bucket("{afk}"));',
        'not_afk = filter_keyvals(afk, "status", ["not-afk"]);',
        f'win = flood(query_bucket("{win}"));',
        "RETURN = filter_period_intersect(win, not_afk);",
    ], start, end)
    work_spans = []
    web = find_bucket(buckets, "aw-watcher-web")
    if web:
        for ev in aw_query([f'RETURN = query_bucket("{web}");'], start, end):
            s = parse_ts(ev["timestamp"])
            work_spans.append((s, s + timedelta(seconds=float(ev.get("duration", 0)))))
    obs = window_observations(events, work_spans, set(cfg.get("browsers", ["Google Chrome"])))
    state["aw_until_next"] = iso(end)
    return obs


# ── git ─────────────────────────────────────────────────────────────────


def repos(roots: list[str], nested: set[str], depth: int = 3) -> list[Path]:
    """Repositories directly under each root, plus any found up to `depth`
    levels down inside `nested` folders (e.g. ~/<team>/<area>/<repo>)."""

    def walk(d: Path, left: int) -> list[Path]:
        if (d / ".git").exists():
            return [d]
        if left == 0:
            return []
        try:
            children = sorted(p for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))
        except OSError:
            return []
        return [r for c in children for r in walk(c, left - 1)]

    out = []
    for root in roots:
        r = Path(root).expanduser()
        if not r.is_dir():
            continue
        if (r / ".git").exists():
            out.append(r)
            continue
        for d in sorted(p for p in r.iterdir() if p.is_dir() and not p.name.startswith(".")):
            if (d / ".git").exists():
                out.append(d)
            elif d.name in nested:
                out += walk(d, depth)
    return out


def repo_email(repo: Path) -> str:
    """The author email configured for this repository (work repos often
    commit with a work address)."""
    r = subprocess.run(["git", "-C", str(repo), "config", "user.email"], capture_output=True, text=True, timeout=10)
    return r.stdout.strip().lower()


def git_observations(repo: Path, since: datetime, emails: set[str], name: str) -> list[dict]:
    fmt = "%x1e%H%x1f%at%x1f%ae%x1f%(trailers:key=Co-Authored-By,valueonly,separator=%x2C)"
    out = subprocess.run(["git", "-C", str(repo), "log", "--all", "--no-merges", "--numstat",
                          f"--since={int(since.timestamp())}", f"--format={fmt}"],
                         capture_output=True, text=True, timeout=60).stdout
    result = []
    for record in out.split("\x1e")[1:]:
        head, _, body = record.partition("\n")
        sha, at, email, coauthors = head.split("\x1f")
        if emails and email.lower() not in emails:
            continue
        exts: dict[str, dict] = {}
        for line in body.strip().splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added, deleted, path = parts
            ext = Path(path.split(" => ")[-1].rstrip("}")).suffix.lower() or "(none)"
            e = exts.setdefault(ext, {"files": 0, "added": 0, "deleted": 0})
            e["files"] += 1
            e["added"] += int(added) if added.isdigit() else 0
            e["deleted"] += int(deleted) if deleted.isdigit() else 0
        ai = any(m in coauthors.lower() for m in AI_COAUTHOR)
        result.append({
            "source": "git", "key": f"{name}:{sha}",  # server-side key becomes git:<repo>:<sha>
            "ts": datetime.fromtimestamp(int(at), timezone.utc).isoformat(),
            "kind": "agent_commit" if ai else "commit",
            "payload": {"repo": name, "files": sum(e["files"] for e in exts.values()),
                        "added": sum(e["added"] for e in exts.values()),
                        "deleted": sum(e["deleted"] for e in exts.values()),
                        "exts": exts, "langs": sorted({LANGS[x] for x in exts if x in LANGS}), "machine": "laptop"},
            "sensitivity": "personal",
        })
    return result


def repo_name(repo: Path, roots: list[str]) -> str:
    """Top-level folder under a root, like the server does (~/<team>/x → <team>)."""
    for root in roots:
        r = Path(root).expanduser()
        try:
            rel = repo.relative_to(r)
        except ValueError:
            continue
        if rel.parts:
            return rel.parts[0]
    return repo.name


def collect_git(cfg: dict, state: dict, now: datetime) -> list[dict]:
    roots = cfg.get("repo_roots", ["~"])
    nested = set(cfg.get("nested_roots", []))
    emails = {e.lower() for e in cfg.get("git_authors", [])}
    if state.get("git_scan_version") != GIT_SCAN_VERSION:
        state["git_until"] = {}
        state["git_scan_version"] = GIT_SCAN_VERSION
    seen = state.setdefault("git_until", {})
    out = []
    for repo in repos(roots, nested):
        last = seen.get(str(repo))
        since = parse_ts(last) - timedelta(hours=1) if last else now - LOOKBACK
        try:
            mine = emails | ({e} if (e := repo_email(repo)) else set())
            obs = git_observations(repo, since, mine, repo_name(repo, roots))
        except (subprocess.SubprocessError, OSError, ValueError) as e:
            log(f"git log failed in {repo}: {e}")
            continue
        out += obs
        seen[str(repo)] = iso(now)
    return out


# ── shipping ────────────────────────────────────────────────────────────


def ingest(cfg: dict, observations: list[dict]) -> int:
    new = 0
    for i in range(0, len(observations), BATCH):
        body = json.dumps({"observations": observations[i : i + BATCH]}).encode()
        req = urllib.request.Request(cfg["ingest_url"], data=body, headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {cfg['token']}"})
        with urllib.request.urlopen(req, timeout=60) as r:
            answer = json.load(r)
        new += answer.get("new", 0)
        for refused in answer.get("refused", [])[:5]:  # kept out by Muninn's contract: said, not sent again
            log(f"muninn refused one: {refused.get('why')}")
    return new


def sync_transcripts(cfg: dict) -> None:
    src, dest = cfg.get("transcripts_src"), cfg.get("transcripts_dest")
    if not (src and dest):
        return
    src_path = Path(src).expanduser()
    if not src_path.is_dir():
        return
    r = subprocess.run(["rsync", "-az", f"{src_path}/", dest], capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        log(f"transcript sync failed: {r.stderr.strip()[:200]}")


def main() -> int:
    cfg = json.loads(CONFIG.read_text())
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    now = datetime.now(timezone.utc)
    observations = collect_windows(cfg, state, now) + collect_git(cfg, state, now)
    try:
        new = ingest(cfg, observations) if observations else 0
    except (urllib.error.URLError, OSError) as e:
        log(f"ingest failed, will retry next run: {e}")
        return 1
    if "aw_until_next" in state:
        state["aw_until"] = state.pop("aw_until_next")
    STATE.write_text(json.dumps(state, indent=1))
    os.chmod(STATE, 0o600)
    sync_transcripts(cfg)
    log(f"shipped {len(observations)} observation(s), {new} new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
