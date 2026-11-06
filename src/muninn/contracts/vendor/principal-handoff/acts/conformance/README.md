# Principal Handoff Acts 0.6 conformance suite

JSON fixtures that any implementation of [Principal Handoff Acts](../SPEC.md) can run. The reference runner is
[`../../reference/test_acts.py`](../../reference/test_acts.py) (`cd ../../reference && python3 -m unittest`). The
fixtures of the [Acts Markdown binding](../../acts-markdown/SPEC.md) are in
[`../../acts-markdown/conformance/`](../../acts-markdown/conformance/).

## Common conventions

- Every file is `{"suite": "acts/0.6", "description": …, "principal": …, "cases": [ … ]}`, where `principal` is the
  configured principal identifier (core R6). Every case has a unique `id`.

## Files

- `records.json` (SPEC §3, core §4–§5): `{id, spec, record, valid, schema, principal_type?}`, with a top-level
  `principal_type` (`natural` or `legal`, core R6) that a case may override. `spec` names the section that decides the
  case; it is metadata and changes no expected result. Valid records name the schema they validate
  against; invalid ones fail the schema or a validity rule of core §4–§7 or SPEC §3. `valid` is `null` for a record of
  an extension or version the receiver does not implement, which it keeps without acting on it (core §4, §6). Records
  with Evidence content are in the [Evidence suite](../../evidence/conformance/).
- `lifecycle.json` (core §6, SPEC §2): `{id, spec, records, expect}`; `records` are all the records one receiver holds
  for one act, in arrival order. `expect` is `{state, outcome, settlement, authority, answer, stale, withdrawn,
  terms_rev, conflict, claims, ignored}`, as the Decisions suite defines them, and does not depend on arrival order. A
  record of a version or extension the receiver does not implement is both kept (core §4, §6) and listed in `ignored`
  (core §10). A
  requester (core §12) compares only `settlement`, `outcome`, `answer`, `authority` and `stale`, computed from the
  need records and the principal side's records alone.
- `relations.json` (core §7): `{id, records, expect}`; `records` are the `related` records one receiver holds
  for one act. `expect` is `{relations, replacement}`: the active relations as `{kind, target}` (target
  members other than `ext`, `project`, `key` and `uri` left out), sorted by kind and target, and the target of
  the single active `replaced_by`, or `null`. Neither depends on arrival order.
- The core's [terms-digest](../../core/conformance/terms-digest.json) and
  [conversion](../../core/conformance/convert.json) vectors apply to every implementation.
