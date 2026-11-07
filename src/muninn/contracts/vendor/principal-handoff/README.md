# Principal Handoff 0.6 (vendored)

The record schemas of Principal Handoff Core 0.6 and its Acts 0.6 and Decisions 0.6 extensions, and the conformance
suites a principal side runs (`records.json`, `lifecycle.json` and `relations.json` of each extension, and core's
`raw.json`, `terms-digest.json` and `convert.json`), copied unchanged from the specification at tag
`principal-handoff-0.6.2` (commit 8f70f0a; 0.6.1 and 0.6.2 add fixtures to 0.6, whose rules and schemas are the same). Licensed
under the Apache License 2.0 (`LICENSE`, `NOTICE`). The Evidence and Criteria modules and the Acts Markdown binding
are not vendored: Muninn implements none of them.

Muninn is the principal side: it receives the projects' need records, claims and notes, and sends its settlements,
relations and notes (`src/muninn/core/ph.py`, `src/muninn/core/handoff_records.py`). It is a converting party (core
section 14): a project configured for 0.5 is checked against `../principal-handoff-0.5/` and told its records in 0.5.
Records received in 0.4 before 0.6 are still read (`../principal-handoff-0.4/`).
