# Principal Handoff 0.4 (vendored)

The record schemas of Principal Handoff Core 0.4 and its Acts 0.4 and Decisions 0.4 extensions, and the parts of
their conformance suites that a principal side runs (`records.json`, `lifecycle.json`, and core's
`terms-digest.json`), copied unchanged from the specification at tag `principal-handoff-0.4` (commit 8d7b988).
Licensed under the Apache License 2.0 (`LICENSE`, `NOTICE`).

Muninn is the principal side: it receives the projects' need records, claims and notes, and sends its settlements,
relations and notes (`src/muninn/core/ph.py`, `src/muninn/core/handoff_records.py`). A project that still sends 0.3
records is read as 0.4 (Acts section 8) and answered with the 0.3 labels until it sends 0.4.
