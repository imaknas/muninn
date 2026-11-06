# Principal Handoff Core 0.6 conformance vectors

JSON vectors for rules of the [core](../SPEC.md) that every extension shares. The reference runner is
[`../../reference/test_core.py`](../../reference/test_core.py) (`cd ../../reference && python3 -m unittest`). The
record, ownership, authority and state rules of the core are exercised through each extension's suite
([Acts](../../acts/conformance/), [Decisions](../../decisions/conformance/)); module rules through each module's
([Evidence](../../evidence/conformance/), [Criteria](../../criteria/conformance/)).

- `terms-digest.json` (SPEC §4): `{"suite": "core/0.6", "description", "cases": [{id, record, canonical, digest}]}`.
  `record` is a need record; `canonical` is the RFC 8785 serialisation of the object with exactly `ext` (the
  extension's name without its version), `project`, `key`, `terms_rev` and `terms`; `digest` is `sha-256:` + the
  lowercase hex SHA-256 of `canonical`'s UTF-8 bytes. The cases cover member order, non-ASCII text, and characters
  JSON must escape (a newline, a quote, a tab, a backslash, U+001F) next to ones it must not (U+007F, `é`, and a
  character outside the Basic Multilingual Plane).
- `raw.json` (SPEC §4): `{"suite": "core/0.6", "description", "principal", "principal_type", "cases": [{id, spec, raw,
  valid, schema}]}`. `spec` names the section that decides the case; it is metadata and changes no expected result.
  `raw` is the text of a record, held in a JSON string, for what a parser loses: duplicate member names, numbers
  written with a fraction or an exponent, unpaired surrogates. Read it with a strict reader and validate what that
  gives under the extension its `ext` names. `valid` is whether the record is valid; `schema` is the type it
  validates as, or `null`. A case whose id begins with `acts-` or `decisions-` is for that extension, and an
  implementation of the other skips it; the rest don't parse or aren't objects, and are invalid for every
  implementation. An escape in the record's text has a doubled backslash in the file: `"\\ud83d"` in the file is the
  six characters `\ud83d` of the record.
- `convert.json` (SPEC §6, §7, §10, §14, Acts §6, Decisions §6, Criteria §10): `{"suite": "core/0.6", "description",
  "principal", "cases": [{id, module?, record, to, expect}], "held": [{id, records, expect}]}`.
  - In `cases`, `record` is converted to version `to` (`"0.5"` or `"0.6"`) from the record alone; `expect` is the
    converted record, or `null` where it has no form in that version. The cases cover need records, settlements and
    notes (`ph` and `ext` change, `evidence.proof` is removed, and `evidence` with it if nothing is left; the terms
    digest is kept), Criteria grants (`"module": "criteria"`, for parties that implement Criteria; unchanged but for
    `ph` and `ext`), and relations of both extensions: `target.ext` keeps only its part before the first `/` going up
    and gets `/0.5` back going down, a `uri` target is unchanged, and a 0.5 target whose extension name or project is
    off-pattern has no 0.6 form.
  - The `held` cases are Acts only: a converting party passes them if it implements Acts (core §12). `records` are what
    a party configured for 0.6 that also implements 0.5 has received about one act, in either version; `expect` is
    `{held, relations}`: the ids of the records it holds (a 0.5 record only if it is valid in 0.5 and has a 0.6 form,
    §10 comparing 0.6 forms) and the act's active relations as `{kind, target}`, sorted. Neither depends on arrival
    order.
