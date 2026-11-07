"""Principal Handoff 0.6 as the principal side reads it, checked against the specification's own conformance cases (tag
principal-handoff-0.6.2): records, raw text, lifecycle, relations, the terms digest and conversion. 0.5, which a
project may still be configured for, against 0.5's record and lifecycle cases; 0.4, still read, against its record
cases."""

from __future__ import annotations

import json

import pytest

from muninn.core import ph
from muninn.package import CONTRACTS

VENDOR = CONTRACTS / "vendor"
ROOT, FIVE, FOUR = VENDOR / "principal-handoff", VENDOR / "principal-handoff-0.5", VENDOR / "principal-handoff-0.4"
EXTS = ph.extensions(VENDOR)


def suite(root, ext: str, name: str) -> dict:
    return json.loads((root / ext / "conformance" / name).read_text(encoding="utf-8"))


def cases(root, version: str, name: str):
    for ext in (ph.ACTS, ph.DECISIONS):
        s = suite(root, ext, name)
        for c in s["cases"]:
            yield pytest.param(ph.label(ext, version), s, c, id=f"{ext}/{version}:{c['id']}")


def parties(s: dict, c: dict) -> ph.Parties:
    return ph.Parties(s["principal"], "muninn", c.get("principal_type", s.get("principal_type")) == "legal")


@pytest.mark.parametrize("ext,s,c", list(cases(ROOT, ph.PH, "records.json")))
def test_records_are_valid_invalid_or_kept_as_the_specification_says(ext, s, c):
    """Three ways: valid, invalid, or kept without being acted on (valid: null). The suite's receiver implements only
    its own extension in 0.6; a record without an `ext` is invalid, not of a version it does not implement."""
    r = c["record"]
    if c["valid"] is None:
        assert isinstance(r.get("ext"), str) and r["ext"] != ext
        return
    assert not isinstance(r.get("ext"), str) or r["ext"] == ext or not c["valid"]
    name, errs = ph.validate(r, EXTS[ext], parties(s, c))
    if c["valid"]:
        assert (name, errs) == (c["schema"], [])
    else:
        assert errs


@pytest.mark.parametrize("ext,s,c", [*cases(FIVE, ph.OLDER, "records.json"), *cases(FOUR, ph.LEGACY, "records.json")])
def test_records_of_the_versions_before_are_valid_or_not_as_their_specification_says(ext, s, c):
    name, errs = ph.validate(c["record"], EXTS[ext], parties(s, c))
    if c["valid"]:
        assert (name, errs) == (c["schema"], [])
    else:
        assert errs


RAW = json.loads((ROOT / "core" / "conformance" / "raw.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("c", RAW["cases"], ids=lambda c: c["id"])
def test_raw_text_is_read_strictly_before_it_is_validated(c):
    try:
        r = ph.loads(c["raw"])
    except ValueError:
        r = None
    if not isinstance(r, dict):
        assert not c["valid"]
        return
    ext = c["id"].split("-")[0]
    e = EXTS[ph.label(ext)] if ext in (ph.ACTS, ph.DECISIONS) else EXTS[ph.label(ph.ACTS)]
    name, errs = ph.validate(r, e, ph.Parties(RAW["principal"], "muninn", RAW.get("principal_type") == "legal"))
    assert (name, errs) == (c["schema"], []) if c["valid"] else errs


@pytest.mark.parametrize("ext,s,c", [*cases(ROOT, ph.PH, "lifecycle.json"), *cases(FIVE, ph.OLDER, "lifecycle.json")])
def test_an_items_state_is_what_the_specification_says_in_any_order(ext, s, c):
    p = ph.Parties(s["principal"], "muninn")
    assert ph.fold(c["records"], EXTS[ext], p) == c["expect"]
    assert ph.fold(list(reversed(c["records"])), EXTS[ext], p) == c["expect"]


@pytest.mark.parametrize("ext,s,c", list(cases(ROOT, ph.PH, "relations.json")))
def test_an_items_active_relations_are_what_the_specification_says_in_any_order(ext, s, c):
    p = ph.Parties(s["principal"], "muninn")
    for records in (c["records"], list(reversed(c["records"]))):
        held = ph.held(records, EXTS[ext], p)
        assert {"relations": ph.relations(held), "replacement": ph.replacement(held)} == c["expect"]


def test_the_terms_digest_is_the_one_the_specification_computes():
    v = json.loads((ROOT / "core" / "conformance" / "terms-digest.json").read_text(encoding="utf-8"))
    for c in v["cases"]:
        assert ph.canonical({"ext": ph.name(c["record"]["ext"]), **{k: c["record"][k] for k in ("project", "key", "terms_rev", "terms")}}) == c["canonical"]
        assert ph.terms_digest(c["record"]) == c["digest"], c["id"]


CONVERT = json.loads((ROOT / "core" / "conformance" / "convert.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("c", [c for c in CONVERT["cases"] if "module" not in c], ids=lambda c: c["id"])  # Criteria is not Muninn's
def test_a_record_converts_between_versions_as_the_specification_says(c):
    assert ph.convert(c["record"], c["to"]) == c["expect"]
    assert ph.converted(c["record"], c["to"]) == c["expect"]


@pytest.mark.parametrize("c", CONVERT["held"], ids=lambda c: c["id"])
def test_what_is_held_of_records_in_either_version_is_what_the_specification_says(c):
    p = ph.Parties(CONVERT["principal"], "muninn")
    for records in (c["records"], list(reversed(c["records"]))):
        held = ph.holding(records, EXTS, p)
        assert {"held": sorted(r["id"] for r in held), "relations": ph.relations(held)} == c["expect"]


def test_a_03_record_reads_as_04_and_on_through_05_as_06():
    raised = {"ph": "0.3", "ext": "acts/0.3", "id": "r:k", "type": "raised", "origin": "requester", "by": "registrar:demo",
              "key": "k", "project": "demo", "ts": "2026-10-27T02:10:00Z", "terms": {"text": "Renew the domain"}, "terms_rev": 1}
    now = ph.converted(raised)
    assert (now["ph"], now["ext"]) == ("0.6", "acts/0.6")
    assert ph.terms_digest(now) == ph.terms_digest(raised)


def test_a_04_settlement_reads_with_the_digest_of_its_need_record():
    raised = {"ph": "0.4", "ext": "acts/0.4", "id": "r:k", "type": "raised", "origin": "requester", "by": "registrar:demo",
              "key": "k", "project": "demo", "ts": "2026-10-27T02:10:00Z", "terms": {"text": "Renew the domain"}, "terms_rev": 1}
    settled = {"ph": "0.4", "ext": "acts/0.4", "id": "s:k:h1", "type": "settled", "origin": "principal", "by": "muninn",
               "key": "k", "project": "demo", "ts": "2026-10-28T08:30:00Z", "terms_rev": 1,
               "terms_digest": ph._terms_digest_04(raised), "outcome": "done", "authority": {"kind": "principal", "decider": "p:owner"}}
    assert ph.converted(settled, ph.PH, [raised])["terms_digest"] == ph.terms_digest(raised)


def test_a_relation_names_an_item_by_its_extension_and_in_05_with_its_version():
    rel = {"ph": "0.6", "ext": "acts/0.6", "id": "rel:k:h2", "type": "related", "origin": "principal", "by": "muninn",
           "key": "k", "project": "demo", "ts": "2026-10-28T08:30:00Z",
           "relation": {"kind": "same_as", "target": {"ext": "acts", "project": "demo", "key": "j"}, "active": True}}
    assert ph.converted(rel, "0.5")["relation"]["target"]["ext"] == "acts/0.5"


def test_a_version_muninn_does_not_implement_is_not_held():
    later = {"ph": "0.7", "ext": "acts/0.7", "id": "r:k", "type": "raised", "origin": "requester", "by": "registrar:demo",
             "key": "k", "project": "demo", "ts": "2026-11-27T02:10:00Z", "terms": {"text": "Renew the domain"}, "terms_rev": 1}
    assert ph.converted(later) is None
    assert ph.holding([later], EXTS, ph.Parties("p:owner", "muninn")) == []


FROM_04 = [c for c in json.loads((FIVE / "core" / "conformance" / "convert.json").read_text(encoding="utf-8"))["cases"]
           if "module" not in c and c["to"] == "0.5" and c["record"].get("ph") == "0.4"]


@pytest.mark.parametrize("c", FROM_04, ids=lambda c: c["id"])
def test_a_record_received_in_04_reads_in_05_as_05s_cases_say(c):
    assert ph.converted(c["record"], ph.OLDER, c["held"]) == c["expect"]


def test_a_member_the_specification_does_not_define_may_be_only_whitespace():
    base = {"ph": "0.6", "ext": "acts/0.6", "id": "w1", "type": "withdrawn", "origin": "requester", "by": "agent:demo",
            "key": "task-demo-aaaaaaaaaa", "project": "demo", "ts": "2026-10-27T09:00:00Z", "reason": "not_needed"}
    p = ph.Parties("p:owner", "muninn")
    assert ph.validate({**base, "x_tag": "  "}, EXTS["acts/0.6"], p)[1] == []  # ignored (core section 4)
    assert ph.validate({**base, "terms": {"text": "Renew", "x_tag": " "}, "type": "raised", "terms_rev": 1}, EXTS["acts/0.6"], p)[1] == []
    assert ph.validate({**base, "reason": " "}, EXTS["acts/0.6"], p)[1]  # one it defines


def test_a_record_listing_itself_is_a_cycle_whichever_record_with_its_id_came_last():
    b = {"ph": "0.6", "ext": "acts/0.6", "key": "task-demo-aaaaaaaaaa", "project": "demo", "origin": "principal", "by": "ps:a"}
    need = {**b, "id": "r1", "type": "raised", "origin": "requester", "by": "agent:demo", "ts": "2026-10-27T09:00:00Z",
            "terms_rev": 1, "terms": {"text": "Which one?"}}
    settled = {**b, "type": "settled", "outcome": "done", "terms_rev": 1, "terms_digest": ph.terms_digest(need),
               "authority": {"kind": "principal", "decider": "p:owner"}}
    reopened = {**b, "id": "s1", "type": "reopened", "ts": "2026-10-28T01:00:00Z", "supersedes": ["s1"], "restore_text": "x"}
    records = [need, reopened, {**settled, "id": "s1", "ts": "2026-10-28T02:00:00Z"},
               {**settled, "id": "s2", "ts": "2026-10-28T03:00:00Z", "supersedes": ["s1"]}]
    p = ph.Parties("p:owner", "muninn")
    for order in (records, records[::-1], [records[0], records[2], records[1], records[3]]):
        f = ph.fold(order, EXTS["acts/0.6"], p)
        assert (f["settlement"], f["conflict"]) == ("s2", True)
