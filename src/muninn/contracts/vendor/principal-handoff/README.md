# Principal Handoff 0.5 (vendored)

The record schemas of Principal Handoff Core 0.5 and its Acts 0.5 and Decisions 0.5 extensions, and the parts of
their conformance suites that a principal side runs (`records.json`, `lifecycle.json`, and core's `terms-digest.json`
and `convert.json`), copied unchanged from the specification at tag `principal-handoff-0.5` (commit 20912e3).
Licensed under the Apache License 2.0 (`LICENSE`, `NOTICE`). The Evidence and Criteria modules and the Acts Markdown
binding are not vendored: Muninn implements none of them.

Muninn is the principal side: it receives the projects' need records, claims and notes, and sends its settlements,
relations and notes (`src/muninn/core/ph.py`, `src/muninn/core/handoff_records.py`). A project configured to speak
0.4 is checked against `../principal-handoff-0.4/` and told its records converted to 0.4 (core section 14).
