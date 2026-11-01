"""Principal Handoff 0.4, as the principal side reads it: is a record well formed and allowed to its emitter
(core sections 3 to 5), what state an item is in from the records held for it, in any order (section 6), and the terms
digest a settlement or an approval is bound to (section 5.3).

An item belongs to one extension (`acts/0.4`: a to-do only the user can do; `decisions/0.4`: a question only the user
can answer). Each extension is described by data (`Ext`): where its schemas are, what happens to an item whose need was
withdrawn while it is still open, the rules its schemas cannot state, and which terms an approval may be in force for.
The schemas are the specification's own, vendored unchanged (contracts/vendor/principal-handoff); the folder they are
read from is given. The reading follows the specification's reference implementation (reference/ph_core.py).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

PH = "0.4"
NEED = ("raised", "amended", "withdrawn")
SETTLEMENT = ("settled", "reopened")
TYPES = NEED + SETTLEMENT + ("approved", "claimed", "related", "note")  # core section 4
OWNER = {"raised": {"requester"}, "amended": {"requester"}, "withdrawn": {"requester"},
         "settled": {"principal"}, "reopened": {"principal"}, "approved": {"principal"}}  # the extensions' schemas narrow it
RELATION_ORIGIN = {"same_as": "principal", "derived_from": "requester", "replaced_by": "requester"}  # core section 7
APPROVAL_PREFIX = "approval:"  # authority.under naming an approval of the same item (core section 5.3)


@dataclass(frozen=True)
class Ext:
    name: str  # "acts/0.4"
    schemas: Path
    withdrawn: str  # "keep": still open, and the user is asked; "stop": no longer presented
    check: Callable[[dict[str, Any]], list[str]] = field(default=lambda r: [])
    approvable: Callable[[dict[str, Any]], bool] = field(default=lambda terms: True)  # terms an approval can be in force for


@dataclass(frozen=True)
class Parties:
    """Who is who, configured rather than asserted (core R6)."""

    principal: str  # the user
    side: str  # Muninn, the instrument the user settles through: never the principal
    legal: bool = False  # the principal is a legal person (then a representative is required)


def _acts(r: dict[str, Any]) -> list[str]:
    text = (r.get("terms") or {}).get("text") if isinstance(r.get("terms"), dict) else None
    return ["terms.text is a single line"] if isinstance(text, str) and ("\n" in text or "\r" in text) else []


def _criteria(terms: dict[str, Any]) -> list[str]:
    """decisions section 2.1: rules on criteria a schema cannot state."""
    c = terms.get("criteria")
    if not isinstance(c, dict):
        return []
    out, checks = [], [x for x in c.get("checks") or [] if isinstance(x, dict)]
    ids = [x.get("id") for x in checks]
    if not ids:
        out.append("criteria need at least one check")
    if len(ids) != len(set(ids)):
        out.append("check ids are unique within the criteria")
    if any(not x.get("trust") for x in checks):
        out.append("every check names at least one trust root")
    option = (c.get("answer") or {}).get("option")
    if option is not None and option not in {o.get("id") for o in terms.get("options") or []}:
        out.append("criteria.answer.option must be one of the options")
    return out


def _decisions(r: dict[str, Any]) -> list[str]:
    t, out = r.get("type"), []
    if t == "settled" and r.get("outcome") == "decided" and "answer" not in r:
        out.append("decided needs answer")
    if t in ("raised", "amended") and isinstance(r.get("terms"), dict):
        ids = [o.get("id") for o in r["terms"].get("options") or [] if isinstance(o, dict)]
        if len(ids) != len(set(ids)):
            out.append("option ids are unique within the terms")
        out += _criteria(r["terms"])
    if t == "settled" and str((r.get("authority") or {}).get("under", "")).startswith(APPROVAL_PREFIX):
        verified = (r.get("evidence") or {}).get("verified") or []
        if r.get("outcome") != "decided":
            out.append("a settlement under an approval records the approved answer")
        if not verified or any(v.get("result") != "valid" for v in verified if isinstance(v, dict)):
            out.append("a settlement under an approval carries only valid verification results")
    return out


ACTS, DECISIONS = f"acts/{PH}", f"decisions/{PH}"
# the version before, which a project may still speak: its records read as this version's (acts section 8: only `ph` and
# `ext` change), and what Muninn sends that project is labelled as it speaks
OLDER, OLDER_EXTS = "0.3", {"acts/0.3": ACTS, "decisions/0.3": DECISIONS}
SPEAKS = "ph:speaks"  # state: by project, the version of the records it last sent (what it is answered in)


def upgraded(r: dict[str, Any]) -> dict[str, Any]:
    """A record as this version reads it: one of the version before, relabelled; any other as it came."""
    return {**r, "ph": PH, "ext": OLDER_EXTS[r["ext"]]} if r.get("ph") == OLDER and r.get("ext") in OLDER_EXTS else r


def labelled(r: dict[str, Any], ph: str) -> dict[str, Any]:
    """A record Muninn made, as a project that speaks `ph` reads it (its content is this version's either way)."""
    if ph != OLDER:
        return r
    back = {new: old for old, new in OLDER_EXTS.items()}
    return {**r, "ph": OLDER, "ext": back.get(r["ext"], r["ext"])}


def extensions(root: Path) -> dict[str, Ext]:
    """The two extensions Muninn implements, their schemas under `root`."""
    return {ACTS: Ext(ACTS, root / "acts" / "schemas", "keep", _acts),
            DECISIONS: Ext(DECISIONS, root / "decisions" / "schemas", "stop", _decisions, lambda terms: "criteria" in terms)}


# ── validation ─────────────────────────────────────────────────────────────

_TYPES = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list), "string": lambda v: isinstance(v, str),
          "boolean": lambda v: isinstance(v, bool), "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
          "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool), "null": lambda v: v is None}


def _ecma(pattern: str) -> str:
    """ECMAScript's $ matches only at the very end of the string; Python's also before a final newline."""
    return re.sub(r"(?<!\\)\$", r"\\Z", pattern)


def _schema_errors(instance: Any, schema: dict[str, Any], base: Path, path: str = "$") -> list[str]:
    """The JSON Schema subset the specification's schemas use: type, required, properties, enum, const, pattern,
    items, and $ref to another schema file."""
    out: list[str] = []
    if "$ref" in schema:
        target = base / schema["$ref"]
        out += _schema_errors(instance, json.loads(target.read_text(encoding="utf-8")), target.parent, path)
    if "type" in schema and not _TYPES[schema["type"]](instance):
        return out + [f"{path}: not {schema['type']}"]
    same = lambda a, b: type(a) is type(b) and a == b  # noqa: E731
    if "const" in schema and not same(instance, schema["const"]):
        out.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and not any(same(instance, v) for v in schema["enum"]):
        out.append(f"{path}: not one of {schema['enum']}")
    if "pattern" in schema and isinstance(instance, str) and not re.search(_ecma(schema["pattern"]), instance):
        out.append(f"{path}: does not match {schema['pattern']}")
    if isinstance(instance, dict):
        out += [f"{path}: missing {k}" for k in schema.get("required", []) if k not in instance]
        for k, sub in schema.get("properties", {}).items():
            if k in instance:
                out += _schema_errors(instance[k], sub, base, f"{path}.{k}")
    if isinstance(instance, list) and "items" in schema:
        for i, v in enumerate(instance):
            out += _schema_errors(v, schema["items"], base, f"{path}[{i}]")
    return out


def validate(r: dict[str, Any], e: Ext, parties: Parties) -> tuple[str | None, list[str]]:
    """The schema the record validates against (its type), and every way it is not a valid record from its emitter."""
    t = r.get("type")
    path = e.schemas / f"{t}.schema.json"
    if t not in TYPES or not path.exists():
        return None, ["no schema for this type"]
    errs = _schema_errors(r, json.loads(path.read_text(encoding="utf-8")), e.schemas)
    if _twice(r):
        errs.append("a member name appears twice (not I-JSON)")
    if not _bounded(r):
        errs.append("numbers are integers within 2^53 - 1")
    times = [r.get("ts")] + [v.get("at") for v in ((r.get("evidence") or {}).get("verified") or []) if isinstance(v, dict)]
    if any(isinstance(x, str) and instant(x) is None for x in times):
        errs.append("a time that is not an RFC 3339 date-time")
    due = (r.get("terms") or {}).get("due") if isinstance(r.get("terms"), dict) else None
    if isinstance(due, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", due):
        try:
            date.fromisoformat(due)
        except ValueError:
            errs.append("terms.due is not a calendar date")
    if t in OWNER and r.get("origin") not in OWNER[t]:  # R1, R2
        errs.append(f"origin {r.get('origin')!r} may not emit {t}")
    if r.get("by") == parties.principal:  # R6: the principal's decisions are recorded by the principal side
        errs.append("the principal identifier cannot emit records")
    rev = r.get("terms_rev")
    if t == "amended" and isinstance(rev, int) and rev < 2:
        errs.append("amended terms_rev must be at least 2")
    if t not in ("raised", "amended") and isinstance(rev, int) and rev < 1:
        errs.append(f"{t} terms_rev must be at least 1")
    if t == "claimed" and r.get("origin") == "principal":  # R3: the owner records, it does not claim
        errs.append("the principal side settles; it does not claim")
    if t == "related":
        errs += _relation(r)
    if t == "settled":
        errs += _authority(r, parties)
        under = str((r.get("authority") or {}).get("under", ""))
        if under.startswith(APPROVAL_PREFIX) and r["authority"].get("kind") != "delegated":
            errs.append("a settlement under an approval is delegated")
        if under.startswith(APPROVAL_PREFIX) and not (e.schemas / "approved.schema.json").exists():
            errs.append("this extension has no approvals")
        if under == APPROVAL_PREFIX:
            errs.append("approval: names no record")
    if t == "approved":
        errs += _approval(r, parties)
    if r.get("adopts") and (t != "settled" or (r.get("authority") or {}).get("kind") not in ("confirmed", "attested")):
        errs.append("adopts only on confirmed or attested settlements")
    errs += _evidence(r)
    errs += e.check(r)
    if t == "note" and len((r.get("about") or {}).keys() & {"key", "quote"}) != 1:
        errs.append("about has exactly one of key and quote")
    if t == "note" and "key" in (r.get("about") or {}) and r["about"]["key"] != r.get("key"):
        errs.append("a note about a key carries that key")
    return t, errs


def _bounded(v: Any) -> bool:
    """core section 4: every number in a record is an integer every receiver serialises alike."""
    if isinstance(v, (bool, str)) or v is None:
        return True
    if isinstance(v, float):
        return False
    if isinstance(v, int):
        return abs(v) <= 2 ** 53 - 1
    if isinstance(v, dict):
        return all(_bounded(x) for x in v.values())
    if isinstance(v, list):
        return all(_bounded(x) for x in v)
    return False


def _relation(r: dict[str, Any]) -> list[str]:
    out, rel = [], r.get("relation") or {}
    want = RELATION_ORIGIN.get(rel.get("kind"))
    if want and r.get("origin") != want:
        out.append(f"{rel.get('kind')} is emitted by {want}")
    target = rel.get("target") or {}
    item = all(k in target for k in ("ext", "project", "key"))
    if item == ("uri" in target) or (not item and target.keys() - {"uri"}):
        out.append("relation target must be an item {ext, project, key} or a {uri}")
    if r.get("origin") == "principal" and item and target["project"] != r.get("project"):
        out.append("the principal side names another project's item only by uri")
    if rel.get("kind") == "replaced_by" and not (
            item and target["ext"] == r.get("ext") and target["project"] == r.get("project") and target["key"] != r.get("key")):
        out.append("replaced_by targets another item of the same extension and project")
    return out


def _authority(r: dict[str, Any], parties: Parties) -> list[str]:
    a, ev = r.get("authority") or {}, r.get("evidence") or {}
    kind, decider = a.get("kind"), a.get("decider")
    out = []
    if kind in ("principal", "confirmed"):
        if decider != parties.principal:
            out.append(f"{kind} needs the principal as decider")
        if parties.legal and "representative" not in a:
            out.append("a legal-person principal needs a representative")
    if kind == "confirmed" and "proposal" not in ev:
        out.append("confirmed needs evidence.proposal")
    if kind in ("delegated", "attested") and decider == parties.principal:
        out.append(f"{kind} decider cannot be the principal")
    if kind == "delegated":
        chain = a.get("chain")
        if "under" not in a or not chain:
            out.append("delegated needs under and chain")
        elif chain[0] != parties.principal or chain[-1] != decider:
            out.append("chain runs from the principal to the decider")
    if kind == "attested" and "basis" not in ev:
        out.append("attested needs evidence.basis")
    return out


def _approval(r: dict[str, Any], parties: Parties) -> list[str]:
    """core section 5.3: an approval in force comes from a person; a retraction only narrows."""
    ap = r.get("approval") or {}
    if not ap.get("active", True):
        out = [] if r.get("supersedes") else ["a retraction lists the approval it retracts in supersedes"]
        return out + (["terms_digest only on a grant"] if "terms_digest" in ap else [])
    out = ["a grant needs terms_rev"] if "terms_rev" not in r else []
    out += ["a grant needs approval.terms_digest"] if "terms_digest" not in ap else []
    a = r.get("authority")
    if not isinstance(a, dict):
        return out + ["an approval needs authority"]
    out += _authority(r, parties)
    if a.get("kind") not in ("principal", "confirmed"):
        out.append("an approval comes from the principal: kind principal or confirmed")
    if "presented" not in (r.get("evidence") or {}):
        out.append("an approval needs evidence.presented")
    return out


def _evidence(r: dict[str, Any]) -> list[str]:
    """core section 5.2: verifiable entries and verification results."""
    ev, t, out = r.get("evidence") or {}, r.get("type"), []
    if not isinstance(ev, dict):
        return out
    entries = ev.get("verifiable")
    if isinstance(entries, list):
        if t not in ("claimed", "note", "settled"):
            out.append("verifiable only on claims, notes and settlements")
        if "terms_rev" not in r:
            out.append("a record with verifiable entries carries terms_rev")
        ids = [x.get("id") for x in entries if isinstance(x, dict)]
        if len(ids) != len(set(ids)):
            out.append("verifiable entry ids are unique within the record")
        if any(isinstance(x, dict) and ("proof" in x) == ("proof_ref" in x) for x in entries):
            out.append("a verifiable entry carries exactly one of proof and proof_ref")
    if "verified" in ev and not (t == "settled" or (t == "note" and r.get("origin") == "principal")):
        out.append("verified only on settlements and principal-side notes")
    return out


# ── time, canonical form, terms digest ─────────────────────────────────────

_TS = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?"
                 r"(?:([Zz])|([+-])([0-9]{2}):([0-9]{2}))")


def instant(ts: str) -> datetime | None:
    """core section 4: an instant, offsets applied, every fractional digit kept; a leap second (:60) compares as the
    last microsecond of its minute. None if ts is not an RFC 3339 date-time with at most six fractional digits."""
    m = _TS.fullmatch(ts)
    if not m:
        return None
    y, mo, d, h, mi, sec = (int(x) for x in m.groups()[:6])
    frac = int((m.group(7) or "0").ljust(6, "0"))
    if sec == 60:
        sec, frac = 59, 999999
    try:
        off = timedelta(0) if m.group(8) else (1 if m.group(9) == "+" else -1) * timedelta(hours=int(m.group(10)), minutes=int(m.group(11)))
        return datetime(y, mo, d, h, mi, sec, frac, tzinfo=timezone(off))
    except ValueError:
        return None


def stamp(t: datetime) -> str:
    """A record's ts as Muninn writes it: UTC, microseconds, Z."""
    return t.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def canonical(v: Any) -> str:
    """RFC 8785 for these records (strings, integers, booleans, null, arrays, objects): member names sorted by their
    UTF-16 code units, no whitespace, strings escaped as JSON.stringify does."""
    if isinstance(v, dict):
        keys = sorted(v, key=lambda k: k.encode("utf-16-be"))
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + canonical(v[k]) for k in keys) + "}"
    if isinstance(v, list):
        return "[" + ",".join(canonical(x) for x in v) + "]"
    return json.dumps(v, ensure_ascii=False)


def terms_digest(need: dict[str, Any]) -> str:
    """core section 5.3: the digest a settlement or an approval binds, over the item and its terms at one terms_rev."""
    bound = {k: need[k] for k in ("ext", "project", "key", "terms_rev", "terms")}
    return "sha-256:" + hashlib.sha256(canonical(bound).encode("utf-8")).hexdigest()


TWICE = "\u0000twice"  # marks an object read with a member name twice: not I-JSON, so its record is invalid


def loads(text: str | bytes) -> Any:
    """JSON as records are read (core section 4, I-JSON): an object with a member name twice is kept marked, so the
    record it is in is refused alone and the others in the same body are read on."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        names = [k for k, _ in items]
        return {**dict(items), TWICE: True} if len(names) != len(set(names)) else dict(items)
    return json.loads(text, object_pairs_hook=pairs)


def _twice(v: Any) -> bool:
    if isinstance(v, dict):
        return TWICE in v or any(_twice(x) for x in v.values())
    return isinstance(v, list) and any(_twice(x) for x in v)


# ── state ──────────────────────────────────────────────────────────────────


def _when(r: dict[str, Any]) -> datetime:
    return instant(r["ts"])  # type: ignore[return-value]  (valid records only)


def _latest(records: list[dict[str, Any]]) -> dict[str, Any]:
    return max(records, key=lambda r: (_when(r), r["id"]))


def _cycle(by_id: dict[str, dict[str, Any]]) -> bool:
    for start in by_id.values():
        seen: set[str] = set()
        stack = [start]
        while stack:
            r = stack.pop()
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            for s in r.get("supersedes", []):
                if s == start["id"]:
                    return True
                if s in by_id:
                    stack.append(by_id[s])
    return False


def _register(records: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, bool]:
    """The current record of one register (the latest head, ties to the greater id) and whether it conflicts."""
    if not records:
        return None, False
    by_id = {r["id"]: r for r in records}
    replaced = {s for r in records for s in r.get("supersedes", []) if s in by_id}
    heads = [r for r in records if r["id"] not in replaced] or records
    return _latest(heads), len(heads) > 1 or _cycle(by_id)


def _approval_in_force(approvals: list[dict[str, Any]], needs: list[dict[str, Any]], need: dict[str, Any] | None,
                       conflict: bool, settlements: list[dict[str, Any]], e: Ext) -> dict[str, Any] | None:
    """core section 6: the approval in force, if any."""
    if need is None or conflict or need["type"] == "withdrawn" or not e.approvable(need["terms"]):
        return None
    rev = need["terms_rev"]
    if rev != max(n["terms_rev"] for n in needs if n["type"] != "withdrawn"):
        return None
    digest = terms_digest(need)
    if any(terms_digest(n) != digest for n in needs if n["type"] != "withdrawn" and n["terms_rev"] == rev):
        return None
    replaced = {s for r in approvals for s in r.get("supersedes", [])}
    used = {r["authority"]["under"][len(APPROVAL_PREFIX):] for r in settlements
            if r["type"] == "settled" and r["authority"].get("under", "").startswith(APPROVAL_PREFIX)}
    live = [r for r in approvals if r["approval"]["active"] and r["id"] not in replaced and r["id"] not in used
            and r["terms_rev"] == rev and r["approval"]["terms_digest"] == digest
            and not any(_when(s) >= _when(r) for s in settlements)]
    return _latest(live) if live else None


def fold(records: list[dict[str, Any]], e: Ext, parties: Parties) -> dict[str, Any]:
    """One item's state from every record held for it, whatever their order. Invalid records are left out (and
    listed); ids are scoped by origin, and an id reused with different valid content keeps the greater canonical form."""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    ignored: set[str] = set()
    for r in records:
        if validate(r, e, parties)[1]:
            if r.get("id") is not None:
                ignored.add(r["id"])
            continue
        old = seen.get((r["origin"], r["id"]))
        if old is None or canonical(r) > canonical(old):
            seen[(r["origin"], r["id"])] = r
    ignored -= {i for _, i in seen}
    valid = list(seen.values())
    needs = [r for r in valid if r["type"] in NEED]
    need, need_conflict = _register(needs)
    settlements = [r for r in valid if r["type"] in SETTLEMENT]
    settle, settle_conflict = _register(settlements)
    terms_rev = max((r.get("terms_rev", 0) for r in valid if r["type"] in ("raised", "amended")), default=0)
    approval = _approval_in_force([r for r in valid if r["type"] == "approved"], needs, need, need_conflict, settlements, e)
    settled = settle if settle and settle["type"] == "settled" else None
    if settled:
        state = "settled"
    elif need and need["type"] == "withdrawn":
        state = "open" if e.withdrawn == "keep" else "withdrawn"
    else:
        state = "open" if need else "unknown"
    return {"state": state, "outcome": settled["outcome"] if settled else None, "settlement": settle["id"] if settle else None,
            "authority": settled["authority"]["kind"] if settled else None, "answer": settled.get("answer") if settled else None,
            "stale": bool(settled and (settled.get("terms_rev", 0) < terms_rev or (
                need is not None and need["type"] != "withdrawn" and need["terms_rev"] == settled["terms_rev"]
                and terms_digest(need) != settled["terms_digest"]))),
            "withdrawn": bool(need and need["type"] == "withdrawn"), "terms_rev": terms_rev,
            "conflict": need_conflict or settle_conflict, "approval": approval["id"] if approval else None,
            "claims": sorted(r["id"] for r in valid if r["type"] == "claimed"), "ignored": sorted(ignored)}
