"""Principal Handoff 0.6, as the principal side reads it: is a record well formed and allowed to its emitter
(core sections 3 to 5), what state an item is in from the records held for it, in any order (sections 6 and 10), which
relations are active (section 7), the terms digest a settlement is bound to (section 4), and a record in the version
before, which a project may still be configured for (section 14).

An item belongs to one extension (`acts`: a to-do only the user can do; `decisions`: a question only the user can
answer), named without its version: the name, the project and the key are the item, and a record id is unique within
the name, the project and its origin (core section 10). Each version of an extension is described by data (`Ext`):
where its schemas are, what happens to an item whose need was withdrawn while it is still open, the rules its schemas
cannot state, and the module records it lists. Muninn thinks in 0.6 and holds the 0.6 form of every record (section
14). A project still configured for 0.5 has its records checked under 0.5's own rules and kept as they were sent; what
Muninn tells it is converted to 0.5, from the record alone. Records received in 0.4 (and 0.3, read as 0.4) before 0.6
are still read, through 0.5, though no project can be told in them any more; older versions are outside 0.6's
specification. A record of a version Muninn does not implement is kept, never acted on, and not held (section 6). The
schemas are the specification's own, vendored unchanged (contracts/vendor/principal-handoff, and principal-handoff-0.5
and -0.4 for the versions before). The reading follows the specification's reference implementation
(reference/ph_core.py).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

PH = "0.6"
OLDER = "0.5"  # the version before: a project may still be configured for it (core section 14)
LEGACY = "0.4"  # received before 0.6, still read through 0.5; never told
OLDEST = "0.3"  # before that: read as 0.4, only `ph` and `ext` changed
VERSIONS = (OLDER, PH)  # what a project can be configured for
NAME = re.compile(r"[a-z][a-z0-9-]*")  # an extension's name (core section 4)
ACTS, DECISIONS = "acts", "decisions"  # the extensions, by name
NEED = ("raised", "amended", "withdrawn")
SETTLEMENT = ("settled", "reopened")
TYPES = NEED + SETTLEMENT + ("claimed", "related", "note")  # core section 4
OWNER = {"raised": {"requester"}, "amended": {"requester"}, "withdrawn": {"requester"},
         "settled": {"principal"}, "reopened": {"principal"}, "approved": {"principal"}}  # the extensions' schemas narrow it
RELATION_ORIGIN = {"same_as": "principal", "derived_from": "requester", "replaced_by": "requester"}  # core section 7
APPROVAL_PREFIX = "approval:"  # 0.4: authority.under naming an approval of the same item


def label(name: str, version: str = PH) -> str:
    """An extension's `ext` in one version: "acts/0.5"."""
    return f"{name}/{version}"


def name(ext: str) -> str:
    """An extension's name, without its version: what scopes an item and a record id."""
    return str(ext).split("/")[0]


@dataclass(frozen=True)
class Parties:
    """Who is who, configured rather than asserted (core R6)."""

    principal: str  # the user
    side: str  # Muninn, the instrument the user settles through: never the principal
    legal: bool = False  # the principal is a legal person (then a representative is required)


Check = Callable[[dict[str, Any], Parties], list[str]]


@dataclass(frozen=True)
class Ext:
    name: str  # "acts/0.6"
    schemas: Path
    withdrawn: str  # "keep": still open, and the user is asked; "stop": no longer presented
    check: Check = field(default=lambda r, parties: [])  # the version's rules a schema cannot state
    types: tuple[str, ...] = TYPES  # the record types its core folds
    modules: Mapping[str, frozenset[str]] = field(default_factory=dict)  # a module's record type -> who may emit it
    relation: Callable[[dict[str, Any]], list[str]] = field(default=lambda r: [])  # the version's rules for a relation
    blank: bool = False  # 0.6 (core section 4): a string member it defines, but `note` and `context`, is never only whitespace


def _acts(r: dict[str, Any], parties: Parties) -> list[str]:
    text = (r.get("terms") or {}).get("text") if isinstance(r.get("terms"), dict) else None
    return ["terms.text is a single line"] if isinstance(text, str) and ("\n" in text or "\r" in text) else []


def _decisions(r: dict[str, Any], parties: Parties) -> list[str]:
    t, out = r.get("type"), []
    if t == "settled" and r.get("outcome") == "decided" and "answer" not in r:
        out.append("decided needs answer")
    if t in ("raised", "amended") and isinstance(r.get("terms"), dict):
        ids = [o.get("id") for o in r["terms"].get("options") or [] if isinstance(o, dict)]
        if len(ids) != len(set(ids)):
            out.append("option ids are unique within the terms")
    return out


def _decisions_06(r: dict[str, Any], parties: Parties) -> list[str]:
    """0.6 (Decisions section 3): option ids and the option an answer names are 1 to 64 characters, none whitespace."""
    out = _decisions(r, parties)
    terms = r.get("terms") if isinstance(r.get("terms"), dict) else {}
    ids = [o.get("id") for o in terms.get("options") or [] if isinstance(o, dict) and isinstance(o.get("id"), str)]
    option = (r.get("answer") or {}).get("option") if isinstance(r.get("answer"), dict) else None
    if any(not OPTION.fullmatch(x) for x in [*ids, *([option] if isinstance(option, str) else [])]):
        out.append("an option id is 1 to 64 characters without whitespace")
    return out


def extensions(vendor: Path) -> dict[str, Ext]:
    """The two extensions Muninn implements, by `ext`: in 0.6 (schemas under `vendor`/principal-handoff), in 0.5
    (principal-handoff-0.5), and in 0.4 (principal-handoff-0.4), only to read what was received in it."""
    six, five, four = vendor / "principal-handoff", vendor / "principal-handoff-0.5", vendor / "principal-handoff-0.4"
    approved = {"approved": frozenset({"principal"})}  # Decisions section 3: the Criteria module's, which Muninn does not implement
    exts = [Ext(label(ACTS), six / "acts" / "schemas", "keep", _acts, relation=_relation, blank=True),
            Ext(label(DECISIONS), six / "decisions" / "schemas", "stop", _decisions_06, modules=approved, relation=_relation, blank=True),
            Ext(label(ACTS, OLDER), five / "acts" / "schemas", "keep", _acts, relation=_relation_05),
            Ext(label(DECISIONS, OLDER), five / "decisions" / "schemas", "stop", _decisions, modules=approved, relation=_relation_05),
            Ext(label(ACTS, LEGACY), four / "acts" / "schemas", "keep", _older(_acts, approvals=False), relation=_relation_05),
            Ext(label(DECISIONS, LEGACY), four / "decisions" / "schemas", "stop", _older(_decisions, approvals=True),
                types=TYPES + ("approved",), relation=_relation_05)]
    return {e.name: e for e in exts}


# ── 0.4's own rules ────────────────────────────────────────────────────────
# What 0.5 moved into its Evidence and Criteria modules was a validity rule in 0.4, and a record a project sends in 0.4
# is checked under 0.4 (core section 14).


def _older(check: Check, *, approvals: bool) -> Check:
    def rules(r: dict[str, Any], parties: Parties) -> list[str]:
        out, t = check(r, parties) + _evidence(r), r.get("type")
        times = [v.get("at") for v in ((r.get("evidence") or {}).get("verified") or []) if isinstance(v, dict)]
        if any(isinstance(x, str) and moment(x) is None for x in times):
            out.append("a time that is not an RFC 3339 date-time")
        under = str((r.get("authority") or {}).get("under", ""))
        if t == "settled" and under.startswith(APPROVAL_PREFIX):
            if r["authority"].get("kind") != "delegated":
                out.append("a settlement under an approval is delegated")
            if under == APPROVAL_PREFIX:
                out.append("approval: names no record")
            if not approvals:
                out.append("this extension has no approvals")
            else:
                verified = (r.get("evidence") or {}).get("verified") or []
                if r.get("outcome") != "decided":
                    out.append("a settlement under an approval records the approved answer")
                if not verified or any(v.get("result") != "valid" for v in verified if isinstance(v, dict)):
                    out.append("a settlement under an approval carries only valid verification results")
        if t == "approved":
            out += _approval(r, parties)
        if approvals and t in ("raised", "amended") and isinstance(r.get("terms"), dict):
            out += _criteria(r["terms"])
        return out
    return rules


def _criteria(terms: dict[str, Any]) -> list[str]:
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


def _approval(r: dict[str, Any], parties: Parties) -> list[str]:
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


# ── validation ─────────────────────────────────────────────────────────────

_TYPES = {"object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list), "string": lambda v: isinstance(v, str),
          "boolean": lambda v: isinstance(v, bool), "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
          "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool), "null": lambda v: v is None}
_WS = "\t\n\v\f\r    -     　﻿"  # ECMAScript's \s
SOLID = re.compile(f"[^{_WS}]")  # a character that is not whitespace (core section 1)
OPTION = re.compile(f"[^{_WS}]{{1,64}}")  # a decision's option id (Decisions section 3)
PROJECT = re.compile(f"[^{_WS}]{{1,128}}")  # a project, as a relation's target names it (core section 7)


def _ecma(pattern: str) -> str:
    r"""ECMAScript's $ matches only at the very end of the string, Python's also before a final newline; and the two
    disagree on what \s is. The schemas use \s and \S outside character classes only."""
    pattern = pattern.replace("\\S", f"[^{_WS}]").replace("\\s", f"[{_WS}]")
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
    """The schema the record validates against (its type), and every way it is not a valid record from its emitter.
    A module's record gets the core's checks only: the rest is the module's (core section 11)."""
    t = r.get("type")
    if not isinstance(t, str):
        return None, ["type is not a string"]
    if t in e.modules:
        core = e.schemas.parent.parent / "core" / "schemas" / "record.schema.json"
        schema = json.loads(core.read_text(encoding="utf-8"))
        errs = _schema_errors(r, schema, core.parent) + _common(r, parties) + _blanks(r, e, schema, core.parent)
        if r.get("ext") != e.name:
            errs.append(f"ext is not {e.name}")
        if r.get("origin") not in e.modules[t]:
            errs.append(f"origin {r.get('origin')!r} may not emit {t}")
        return None, errs  # a module's pair has no schema of its own in the extension
    path = e.schemas / f"{t}.schema.json"
    if t not in e.types or not path.exists():
        return None, ["no schema for this type"]
    schema = json.loads(path.read_text(encoding="utf-8"))
    errs = _schema_errors(r, schema, e.schemas)
    if errs:  # not the shape the rules below read
        return t, errs
    errs += _common(r, parties) + _blanks(r, e, schema, e.schemas)
    due = (r.get("terms") or {}).get("due") if isinstance(r.get("terms"), dict) else None
    if isinstance(due, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", due):
        try:
            date.fromisoformat(due)
        except ValueError:
            errs.append("terms.due is not a calendar date")
    if t in OWNER and r.get("origin") not in OWNER[t]:  # R1, R2
        errs.append(f"origin {r.get('origin')!r} may not emit {t}")
    rev = r.get("terms_rev")
    if t == "amended" and isinstance(rev, int) and rev < 2:
        errs.append("amended terms_rev must be at least 2")
    if t not in ("raised", "amended") and isinstance(rev, int) and rev < 1:
        errs.append(f"{t} terms_rev must be at least 1")
    if t == "claimed" and r.get("origin") == "principal":  # R3: the owner records, it does not claim
        errs.append("the principal side settles; it does not claim")
    if t == "related":
        errs += e.relation(r)
    if t == "settled":
        errs += _authority(r, parties)
    if "adopts" in r and (t != "settled" or (r.get("authority") or {}).get("kind") not in ("confirmed", "attested")):
        errs.append("adopts only on confirmed or attested settlements")
    errs += e.check(r, parties)
    if t == "note" and len((r.get("about") or {}).keys() & {"key", "quote"}) != 1:
        errs.append("about has exactly one of key and quote")
    if t == "note" and "key" in (r.get("about") or {}) and r["about"]["key"] != r.get("key"):
        errs.append("a note about a key carries that key")
    return t, errs


def _blanks(r: dict[str, Any], e: Ext, schema: dict[str, Any], base: Path) -> list[str]:
    return ["a string member is only whitespace"] if e.blank and _blank(r, schema, base) else []


def _blank(v: Any, schema: dict[str, Any], base: Path, member: str = "") -> bool:
    """A string member the schemas define (a member they do not name is ignored, core section 4), other than `note` and
    `context`, with no character that is not whitespace."""
    if "$ref" in schema:
        target = base / schema["$ref"]
        if _blank(v, json.loads(target.read_text(encoding="utf-8")), target.parent, member):
            return True
    if isinstance(v, str):
        return member not in ("note", "context") and not SOLID.search(v)
    if isinstance(v, dict):
        return any(_blank(v[k], sub, base, k) for k, sub in schema.get("properties", {}).items() if k in v)
    if isinstance(v, list) and "items" in schema:
        return any(_blank(x, schema["items"], base, member) for x in v)
    return False


def _common(r: dict[str, Any], parties: Parties) -> list[str]:
    """What every record is checked for, a module's too (core sections 4, 11 and R6)."""
    errs = []
    if _twice(r):
        errs.append("a member name appears twice (not I-JSON)")
    if _surrogate(r):
        errs.append("a string has an unpaired surrogate (not I-JSON)")
    if not _bounded(r):
        errs.append("numbers are integers within 2^53 - 1")
    if isinstance(r.get("ts"), str) and moment(r["ts"]) is None:
        errs.append("ts is not an RFC 3339 date-time")
    if r.get("by") == parties.principal:  # R6: the principal's decisions are recorded by the principal side
        errs.append("the principal identifier cannot emit records")
    return errs


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


def _relation(r: dict[str, Any], same_ext: Callable[[str, str], bool] = lambda target, ext: target == name(ext)) -> list[str]:
    """core section 7: who may relate items how, and to what. In 0.6 a target names an item's extension by its name."""
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
            item and same_ext(target["ext"], str(r.get("ext"))) and target["project"] == r.get("project") and target["key"] != r.get("key")):
        out.append("replaced_by targets another item of the same extension and project")
    return out


def _relation_05(r: dict[str, Any]) -> list[str]:
    """0.5 and before: a replaced_by names its target's extension as the record's whole `ext`."""
    return _relation(r, lambda target, ext: target == ext)


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


# ── time, canonical form, terms digest ─────────────────────────────────────

_TS = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})[Tt]([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,6}))?"
                 r"(?:([Zz])|([+-])([0-9]{2}):([0-9]{2}))")


def moment(ts: Any) -> int | None:
    """core section 4: an instant, as microseconds from the start of year 0 (UTC, proleptic Gregorian), offsets applied
    and every fractional digit kept; a leap second (:60) compares as the last microsecond of its minute. None if ts is
    not an RFC 3339 date-time with at most six fractional digits (an offset's hour at most 23 and minute at most 59)."""
    m = _TS.fullmatch(ts) if isinstance(ts, str) else None
    if not m:
        return None
    y, mo, d, h, mi, sec = (int(x) for x in m.groups()[:6])
    frac = int((m.group(7) or "0").ljust(6, "0"))
    leap = y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)
    days_in = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    off_h, off_m = (int(m.group(10)), int(m.group(11))) if not m.group(8) else (0, 0)
    if not (1 <= mo <= 12 and 1 <= d <= days_in[mo - 1] and h <= 23 and mi <= 59 and sec <= 60 and off_h <= 23 and off_m <= 59):
        return None
    if sec == 60:
        sec, frac = 59, 999999
    days = 365 * y + (y + 3) // 4 - (y + 99) // 100 + (y + 399) // 400 + sum(days_in[:mo - 1]) + d - 1  # leap days before year y
    offset = (1 if m.group(9) == "+" else -1) * (off_h * 60 + off_m) if not m.group(8) else 0
    return ((days * 24 + h) * 60 + mi - offset) * 60_000_000 + sec * 1_000_000 + frac


def instant(ts: str) -> datetime | None:
    """`ts` as a datetime, for Muninn's own times (None when it is not a valid ts, or before year 1)."""
    m = _TS.fullmatch(ts) if moment(ts) is not None else None
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
    """core section 4: the digest a settlement binds, over the item (its extension by name) and its terms at one
    terms_rev."""
    bound = {"ext": name(need["ext"]), **{k: need[k] for k in ("project", "key", "terms_rev", "terms")}}
    return "sha-256:" + hashlib.sha256(canonical(bound).encode("utf-8")).hexdigest()


def _terms_digest_04(need: dict[str, Any]) -> str:
    """0.4's terms digest, over the full `ext` (its version included): only to convert to and from 0.4."""
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


def _surrogate(v: Any) -> bool:
    """A string, or a member name, with half a surrogate pair: JSON's escapes allow it, I-JSON (core section 4) not."""
    if isinstance(v, str):
        return any("\ud800" <= c <= "\udfff" for c in v)
    if isinstance(v, dict):
        return any(_surrogate(k) or _surrogate(x) for k, x in v.items())
    return isinstance(v, list) and any(_surrogate(x) for x in v)


def _twice(v: Any) -> bool:
    if isinstance(v, dict):
        return TWICE in v or any(_twice(x) for x in v.values())
    return isinstance(v, list) and any(_twice(x) for x in v)


# ── versions (core section 14) ─────────────────────────────────────────────


def version(r: dict[str, Any]) -> str:
    """The version a record's `ext` names ("" when it names none)."""
    return str(r.get("ext") or "").partition("/")[2]


def upgraded(r: dict[str, Any]) -> dict[str, Any]:
    """A 0.3 record as 0.4 reads it (only `ph` and `ext` change); any other as it came."""
    if r.get("ph") == OLDEST and r.get("ext") in (label(ACTS, OLDEST), label(DECISIONS, OLDEST)):
        return {**r, "ph": LEGACY, "ext": label(name(r["ext"]), LEGACY)}
    return r


def _without_proof(r: dict[str, Any], out: dict[str, Any]) -> dict[str, Any]:
    """`evidence.proof` removed, and `evidence` with it when nothing else is left (core section 14)."""
    if "evidence" in r:
        ev = {k: v for k, v in (r.get("evidence") or {}).items() if k != "proof"}
        if ev:
            out["evidence"] = ev
        else:
            del out["evidence"]
    return out


def convert(r: dict[str, Any], to: str) -> dict[str, Any] | None:
    """A 0.5 record in 0.6, or a 0.6 one in 0.5, from the record alone (core section 14): `ph` and `ext` changed,
    `evidence.proof` removed, and a relation's target named by its extension's name in 0.6 (with "/0.5" in 0.5). None
    when it has no form in `to`: a 0.5 relation whose target 0.6 cannot name."""
    out = _without_proof(r, {**r, "ph": to, "ext": label(name(str(r.get("ext"))), to)})
    target = (r.get("relation") or {}).get("target") if r.get("type") == "related" and isinstance(r.get("relation"), dict) else None
    if isinstance(target, dict) and isinstance(target.get("ext"), str):
        if to == PH:
            ext = target["ext"].split("/")[0]
            if not NAME.fullmatch(ext) or not (isinstance(target.get("project"), str) and PROJECT.fullmatch(target["project"])):
                return None
        else:
            ext = label(target["ext"], OLDER)
        out["relation"] = {**r["relation"], "target": {**target, "ext": ext}}
    return out


def _from_04(r: dict[str, Any], held: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """A 0.4 record as 0.5 reads it: `evidence.proof` removed, a settlement's terms digest that of the held need record
    at its terms_rev whose 0.4 digest matches it (kept when none does), and a relation naming an item of its own
    extension naming it in 0.5."""
    ext = name(r["ext"])
    out = _without_proof(r, {**r, "ph": OLDER, "ext": label(ext, OLDER)})
    if r.get("type") == "settled" and "terms_digest" in r:
        needs = [upgraded(n) for n in held if n.get("type") in ("raised", "amended") and n.get("terms_rev") == r.get("terms_rev")]
        match = [n for n in needs if _terms_digest_04({**n, "ext": r["ext"]}) == r["terms_digest"]]
        if match:
            out["terms_digest"] = terms_digest(match[0])
    target = (r.get("relation") or {}).get("target") or {}
    if "ext" in target and name(target["ext"]) == ext:
        out["relation"] = {**r["relation"], "target": {**target, "ext": out["ext"]}}
    return out


def converted(r: dict[str, Any], to: str = PH, held: Iterable[dict[str, Any]] = ()) -> dict[str, Any] | None:
    """`r`, in any version Muninn reads, in `to` (0.6 or 0.5), given the records held about its item (a 0.4
    settlement's digest is that of its need record); None when it has no form there, or is of a version Muninn does
    not read (kept, not held: core section 6)."""
    r = upgraded(r)
    if version(r) == LEGACY and r.get("ph") == LEGACY:
        r = _from_04(r, list(held))
    if version(r) not in VERSIONS or r.get("ph") != version(r):
        return None
    if version(r) == to:
        return r if "proof" not in (r.get("evidence") or {}) else _without_proof(r, dict(r))
    return convert(r, to)


# ── state ──────────────────────────────────────────────────────────────────


def _when(r: dict[str, Any]) -> int:
    return moment(r["ts"])  # type: ignore[return-value]  (valid records only)


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
    """The current record of one register (the latest head, ties to the greater id) and whether it conflicts. A
    record that lists itself is still a head, and a cycle."""
    if not records:
        return None, False
    by_id = {r["id"]: r for r in records}
    replaced = {s for r in records for s in r.get("supersedes", []) if s in by_id and s != r["id"]}
    heads = [r for r in records if r["id"] not in replaced] or records
    return _latest(heads), len(heads) > 1 or _cycle(by_id)


def held(records: Iterable[dict[str, Any]], e: Ext, parties: Parties) -> list[dict[str, Any]]:
    """The valid records of the types the core folds, one per (origin, type, id): an invalid record never blocks a
    valid one with the same id, an id reused with different valid content keeps the greater canonical form, and a
    record never displaces one of another type (core section 10)."""
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in records:
        if r.get("type") not in e.types or validate(r, e, parties)[1]:
            continue
        k = (r["origin"], r["type"], r["id"])
        if k not in seen or canonical(r) > canonical(seen[k]):
            seen[k] = r
    return list(seen.values())


def fold(records: list[dict[str, Any]], e: Ext, parties: Parties) -> dict[str, Any]:
    """One item's state from every record held for it, whatever their order (core section 6). Invalid records are
    left out and listed; a module's records are its own, neither folded nor listed (section 11)."""
    valid = held(records, e, parties)
    ignored = {r["id"] for r in records if r.get("id") is not None and validate(r, e, parties)[1]} - {r["id"] for r in valid}
    needs = [r for r in valid if r["type"] in NEED]
    need, need_conflict = _register(needs)
    settle, settle_conflict = _register([r for r in valid if r["type"] in SETTLEMENT])
    terms_rev = max((r.get("terms_rev", 0) for r in valid if r["type"] in ("raised", "amended")), default=0)
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
            "conflict": need_conflict or settle_conflict,
            "claims": sorted(r["id"] for r in valid if r["type"] == "claimed"), "ignored": sorted(ignored)}


def holding(records: Iterable[dict[str, Any]], exts: Mapping[str, Ext], parties: Parties) -> list[dict[str, Any]]:
    """What Muninn holds about one item from the records kept for it, in whichever version each came (core sections 6
    and 14): each valid under its own version and with a 0.6 form, that form, one per (origin, type, id), section 10
    comparing the 0.6 forms. A record of a version Muninn does not implement is kept but not held."""
    records = list(records)
    seen: dict[tuple[str, str, str], dict[str, Any]] = {}
    for r in records:
        e = exts.get(str(upgraded(r).get("ext")))
        if e is None or r.get("type") not in e.types or validate(upgraded(r), e, parties)[1]:
            continue
        form = converted(r, PH, records)
        if form is None:
            continue
        k = (form["origin"], form["type"], form["id"])
        if k not in seen or canonical(form) > canonical(seen[k]):
            seen[k] = form
    return list(seen.values())


def relations(held: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """core section 7: an item's active relations, from the `related` records held for it. A relation is its kind and
    its target's extension name, project and key, or its kind and uri (an exact string); the latest record of it
    (equal ts: the greater id) says whether it is active. A same_as or derived_from naming the item itself has no
    effect. As {kind, target}, sorted."""
    latest: dict[tuple[str, ...], dict[str, Any]] = {}
    for r in held:
        if r.get("type") != "related":
            continue
        rel, target = r["relation"], r["relation"]["target"]
        k = (rel["kind"], "uri", target["uri"]) if "uri" in target else (rel["kind"], name(target["ext"]), target["project"], target["key"])
        if k not in latest or (_when(r), r["id"]) > (_when(latest[k]), latest[k]["id"]):
            latest[k] = r
    out = []
    for k in sorted(latest):
        r = latest[k]
        target = {m: v for m, v in r["relation"]["target"].items() if m in ("uri", "ext", "project", "key")}
        if r["relation"]["active"] and target != {"ext": name(r["ext"]), "project": r["project"], "key": r["key"]}:
            out.append({"kind": k[0], "target": target})
    return out


def replacement(held: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """core section 7: the target of the item's single active replaced_by; with more than one, it has none."""
    active = [x["target"] for x in relations(held) if x["kind"] == "replaced_by"]
    return active[0] if len(active) == 1 else None
