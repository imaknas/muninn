"""What the user does on their computers, from whichever collector runs there (contracts/activity.v1.md): which
window was in front and for how long, and the commits they made. A collector posts these to /api/ingest; the core
reads only the fields written here, whoever sent them, so a collector for another system is another sender of the
same thing. What does not keep to it is refused with the reason, not kept half-read; the rest of the same post is
kept, so a collector moves on instead of sending the same post again forever.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping

from .models import Observation, Sensitivity

SOURCE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")  # a collector's name, e.g. "aw"
SHA = re.compile(r"[0-9a-f]{7,64}")
PROFILES = ("work", "personal")  # which browser profile a browser window was in
DAY_S = 86400


class Refused(ValueError):
    """An observation that does not keep to the contract: why."""


def _ts(raw: Any) -> datetime:
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError as e:
        raise Refused(f"ts: not an ISO 8601 time ({raw!r})") from e
    if ts.tzinfo is None:
        raise Refused("ts: needs a time zone")
    return ts


def _count(p: Mapping[str, Any], field: str) -> int:
    v = p.get(field, 0)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        raise Refused(f"payload.{field}: a whole number ≥ 0")
    return v


def _window(raw: Mapping[str, Any], p: Mapping[str, Any]) -> dict[str, Any]:
    app = p.get("app")
    if not isinstance(app, str):
        raise Refused("payload.app: the application's name (empty when the collector could not tell)")
    if not isinstance(p.get("title", ""), str):
        raise Refused("payload.title: text (may be empty)")
    dur = p.get("duration_s")
    if isinstance(dur, bool) or not isinstance(dur, (int, float)) or not 0 <= dur <= DAY_S:
        raise Refused("payload.duration_s: seconds in front, 0 to 86400")
    out: dict[str, Any] = {"app": app, "title": p.get("title", ""), "duration_s": dur}
    if "browser_profile" in p:
        if p["browser_profile"] not in PROFILES:
            raise Refused(f"payload.browser_profile: one of {', '.join(PROFILES)}")
        out["browser_profile"] = p["browser_profile"]
    if isinstance(p.get("device"), str) and p["device"]:
        out["device"] = p["device"]
    return out


def _commit(raw: Mapping[str, Any], p: Mapping[str, Any]) -> dict[str, Any]:
    if raw["source"] != "git":
        raise Refused('source: commits come from "git" (so one made on two machines is counted once)')
    repo = p.get("repo")
    if not isinstance(repo, str) or not repo or ":" in repo:
        raise Refused("payload.repo: the repository's name (no ':')")
    sha = str(raw["key"]).removeprefix(f"{repo}:")
    if str(raw["key"]) != f"{repo}:{sha}" or not SHA.fullmatch(sha):
        raise Refused("key: <repo>:<commit sha>, as Muninn's own git source writes it")
    exts = p.get("exts", {})
    if not isinstance(exts, Mapping) or any(not isinstance(v, Mapping) for v in exts.values()):
        raise Refused("payload.exts: file extension → {files, added, deleted}")
    langs = p.get("langs", [])
    if not isinstance(langs, list) or any(not isinstance(x, str) for x in langs):
        raise Refused("payload.langs: a list of language names")
    out: dict[str, Any] = {"repo": repo, "files": _count(p, "files"), "added": _count(p, "added"), "deleted": _count(p, "deleted"),
                           "exts": {str(k): {f: _count(v, f) for f in ("files", "added", "deleted")} for k, v in exts.items()},
                           "langs": langs}
    if isinstance(p.get("machine"), str) and p["machine"]:
        out["machine"] = p["machine"]
    return out


KINDS = {"window": _window, "commit": _commit, "agent_commit": _commit}


def observations(raws: Any) -> tuple[list[Observation], list[dict[str, Any]]]:
    """The observations in one post that keep to the contract, and for each that does not, where it was and why."""
    if not isinstance(raws, list):
        raise Refused("observations: a list")
    kept, refused = [], []
    for i, raw in enumerate(raws):
        try:
            kept.append(observation(raw))
        except Refused as e:
            refused.append({"index": i, "why": str(e)})
    return kept, refused


def observation(raw: Any) -> Observation:
    """One posted observation as Muninn keeps it: only the contract's fields, sensitive unless it says less (the
    attribution can only raise it). Refused, with why, when it does not keep to the contract."""
    if not isinstance(raw, Mapping):
        raise Refused("an observation is a JSON object")
    for field in ("source", "key", "ts", "kind"):
        if not raw.get(field):
            raise Refused(f"{field}: missing")
    if not SOURCE.fullmatch(str(raw["source"])):
        raise Refused("source: the collector's name, lowercase letters, digits, - and _")
    if raw["kind"] not in KINDS:
        raise Refused(f"kind: {raw['kind']!r}; one of {', '.join(KINDS)}")
    if len(str(raw["key"])) > 200:
        raise Refused("key: at most 200 characters")
    p = raw.get("payload")
    if not isinstance(p, Mapping):
        raise Refused("payload: a JSON object")
    try:
        sensitivity = Sensitivity.parse(raw.get("sensitivity"), Sensitivity.SENSITIVE)
    except (ValueError, KeyError, AttributeError) as e:
        raise Refused(f"sensitivity: {raw.get('sensitivity')!r}") from e
    return Observation(source=str(raw["source"]), ts=_ts(raw["ts"]), kind=str(raw["kind"]),
                       key=f"{raw['source']}:{raw['key']}", payload=KINDS[raw["kind"]](raw, p), sensitivity=sensitivity)
