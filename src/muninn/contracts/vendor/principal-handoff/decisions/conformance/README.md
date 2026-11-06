# Principal Handoff Decisions 0.6 conformance suite

JSON fixtures that any implementation of [Principal Handoff Decisions](../SPEC.md) can run. The reference runner is
[`../../reference/test_decisions.py`](../../reference/test_decisions.py) (`cd ../../reference && python3 -m
unittest`).

Every file is `{"suite": "decisions/0.6", "principal": …, "description": …, "cases": [ … ]}`; `principal` is the
configured principal identifier (core R6).

- `records.json` (SPEC §3, core §4–§5): `{id, spec, record, valid, schema, principal_type?}`, with a top-level
  `principal_type` (`natural` or `legal`, core R6) that a case may override. `spec` names the section that decides the
  case; it is metadata and changes no expected result. Valid records name the schema they validate
  against; `schema` is `null` for the others, and for a module pair, which has no schema of its own (SPEC §3). Invalid
  ones fail the schema or a validity rule: ownership (core R1–R3, R6), authority (core §5.1), the
  record rules of core §4 and §7, and that `decided` carries an `answer`. `valid` is `null` for a record of an extension
  or version the receiver does not implement, which it keeps without acting on it (core §4, §6). Records with module
  content are in the [Evidence](../../evidence/conformance/) and [Criteria](../../criteria/conformance/) suites; the
  `approved` cases here check only the pair and the core checks (core §11).
- `lifecycle.json` (core §6, SPEC §2 and §4): `{id, spec, records, expect}`; `records` are all the records one
  receiver holds for one decision, in arrival order (duplicates, revisions, forks, reopening, withdrawal, stale terms,
  claims, invalid records). `expect` is `{state, outcome, settlement, authority, answer, stale, withdrawn, terms_rev,
  conflict, claims, ignored}`. A withdrawn decision has state `withdrawn` unless a settlement is current. `terms_rev`
  is the highest `terms_rev` among the held `raised` and `amended` (0 when none is held); `claims` lists the ids of
  the valid claims held, sorted; `ignored` lists the ids of the invalid records, module records included, sorted (valid
  module records are left to their module and not listed; a record with no string `id` has none to list). A record of
  a version or extension the receiver does not implement is both kept (core §4, §6) and listed in `ignored` (core
  §10). `expect` does not depend on arrival order: the reference runner also folds each case reversed.
- `relations.json` (core §7): `{id, records, expect}`; `records` are the `related` records one receiver holds
  for one decision. `expect` is `{relations, replacement}`: the active relations as `{kind, target}` (target
  members other than `ext`, `project`, `key` and `uri` left out), sorted by kind and target, and the target of
  the single active `replaced_by`, or `null`. Neither depends on arrival order.

A principal side runs every file in full. A requester (core §12) runs `records.json` and compares `lifecycle.json`
only on `settlement`, `outcome`, `answer`, `authority` and `stale`, computed from the need records and the principal
side's records alone. The core's [terms-digest](../../core/conformance/terms-digest.json) and
[conversion](../../core/conformance/convert.json) vectors apply to every implementation.
