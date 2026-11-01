"""Principal Handoff 0.5 as the principal side reads it, checked against the specification's own conformance cases;
and 0.4, which a project may still speak, against 0.4's record cases."""

from __future__ import annotations

import json

import pytest

from muninn.core import ph
from muninn.package import CONTRACTS

ROOT, OLDER = CONTRACTS / "vendor" / "principal-handoff", CONTRACTS / "vendor" / "principal-handoff-0.4"
EXTS = ph.extensions(ROOT, OLDER)


def cases(root, version: str, name: str):
    for ext in (ph.ACTS, ph.DECISIONS):
        s = json.loads((root / ext / "conformance" / name).read_text(encoding="utf-8"))
        for c in s["cases"]:
            yield pytest.param(ph.label(ext, version), s, c, id=f"{ext}/{version}:{c['id']}")


@pytest.mark.parametrize("ext,s,c", [*cases(ROOT, ph.PH, "records.json"), *cases(OLDER, ph.OLDER, "records.json")])
def test_records_are_valid_or_not_as_the_specification_says(ext, s, c):
    parties = ph.Parties(s["principal"], "muninn", c.get("principal_type", s.get("principal_type")) == "legal")
    name, errs = ph.validate(c["record"], EXTS[ext], parties)
    if c["valid"]:
        assert (name, errs) == (c["schema"], [])
    else:
        assert errs


@pytest.mark.parametrize("ext,s,c", list(cases(ROOT, ph.PH, "lifecycle.json")))
def test_an_items_state_is_what_the_specification_says_in_any_order(ext, s, c):
    parties = ph.Parties(s["principal"], "muninn")
    assert ph.fold(c["records"], EXTS[ext], parties) == c["expect"]
    assert ph.fold(list(reversed(c["records"])), EXTS[ext], parties) == c["expect"]


def test_the_terms_digest_is_the_one_the_specification_computes():
    v = json.loads((ROOT / "core" / "conformance" / "terms-digest.json").read_text(encoding="utf-8"))
    for c in v["cases"]:
        assert ph.terms_digest(c["record"]) == c["digest"], c["id"]


CONVERT = json.loads((ROOT / "core" / "conformance" / "convert.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("c", [c for c in CONVERT if "module" not in c], ids=lambda c: c["id"])  # Criteria is not Muninn's
def test_a_record_converts_between_versions_as_the_specification_says(c):
    assert ph.convert(c["record"], c["to"], c["held"]) == c["expect"]
    assert ph.converted(c["record"], c["to"], c["held"]) == c["expect"]


def test_a_03_record_reads_as_04_and_one_told_to_a_03_project_is_labelled_03():
    raised = {"ph": "0.3", "ext": "acts/0.3", "id": "r:k", "type": "raised", "origin": "requester", "by": "registrar:demo",
              "key": "k", "project": "demo", "ts": "2026-10-27T02:10:00Z", "terms": {"text": "Renew the domain"}, "terms_rev": 1}
    now = ph.converted(raised)
    assert (now["ph"], now["ext"]) == ("0.5", "acts/0.5")
    settled = {"ph": "0.5", "ext": "acts/0.5", "id": "s:k:h1", "type": "settled", "origin": "principal", "by": "muninn",
               "key": "k", "project": "demo", "ts": "2026-10-28T08:30:00Z", "terms_rev": 1, "terms_digest": ph.terms_digest(now),
               "outcome": "done", "authority": {"kind": "principal", "decider": "p:owner"}}
    told = ph.converted(settled, "0.3", [raised])
    assert (told["ph"], told["ext"]) == ("0.3", "acts/0.3")
    assert told["terms_digest"] == ph.converted(settled, "0.4", [raised])["terms_digest"] != settled["terms_digest"]


def test_a_relation_to_an_item_of_the_same_extension_names_it_in_the_version_told():
    rel = {"ph": "0.5", "ext": "acts/0.5", "id": "rel:k:h2", "type": "related", "origin": "principal", "by": "muninn",
           "key": "k", "project": "demo", "ts": "2026-10-28T08:30:00Z",
           "relation": {"kind": "same_as", "target": {"ext": "acts/0.5", "project": "demo", "key": "j"}, "active": True}}
    assert ph.converted(rel, "0.4")["relation"]["target"]["ext"] == "acts/0.4"
