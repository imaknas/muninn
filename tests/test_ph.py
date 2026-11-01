"""Principal Handoff 0.4 as the principal side reads it, checked against the specification's own conformance cases."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from muninn.core import ph

ROOT = Path(__file__).resolve().parents[1] / "contracts" / "vendor" / "principal-handoff"
EXTS = ph.extensions(ROOT)


def suite(ext: str, name: str) -> dict:
    return json.loads((ROOT / ext.split("/")[0] / "conformance" / name).read_text(encoding="utf-8"))


def cases(name: str):
    for ext in EXTS:
        s = suite(ext, name)
        for c in s["cases"]:
            yield pytest.param(ext, s, c, id=f"{ext}:{c['id']}")


@pytest.mark.parametrize("ext,s,c", list(cases("records.json")))
def test_records_are_valid_or_not_as_the_specification_says(ext, s, c):
    parties = ph.Parties(s["principal"], "muninn", c.get("principal_type", s.get("principal_type")) == "legal")
    name, errs = ph.validate(c["record"], EXTS[ext], parties)
    if c["valid"]:
        assert (name, errs) == (c["schema"], [])
    else:
        assert errs


@pytest.mark.parametrize("ext,s,c", list(cases("lifecycle.json")))
def test_an_items_state_is_what_the_specification_says_in_any_order(ext, s, c):
    parties = ph.Parties(s["principal"], "muninn")
    assert ph.fold(c["records"], EXTS[ext], parties) == c["expect"]
    assert ph.fold(list(reversed(c["records"])), EXTS[ext], parties) == c["expect"]


def test_the_terms_digest_is_the_one_the_specification_computes():
    v = json.loads((ROOT / "core" / "conformance" / "terms-digest.json").read_text(encoding="utf-8"))
    for c in v["cases"]:
        if "digest" in c:
            assert ph.terms_digest(c["record"]) == c["digest"], c.get("id")
