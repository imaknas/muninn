# Muninn ↔ projects, v2: Principal Handoff 0.3 records

Muninn is the **principal side** of Principal Handoff Core 0.3 with the Acts 0.3 and Decisions 0.3 extensions
(vendored unchanged in `vendor/principal-handoff/`, schemas and the records/lifecycle conformance cases, which Muninn's
implementation passes). v2 runs beside v1 (`messages.v1.schema.json`, `handoffs.v1.schema.json`) until the projects'
side reads and writes only v2; then v1 is retired.

## Parties (core R6)

- The principal identifier is configured (`principal_id`); unset, it is `person:<owner_label>` in lower case.
- Muninn's own party identifier is `muninn`. Its records have `origin: "principal"`, `by: "muninn"`.
- The projects' side authenticates with the same bearer token as `/api/messages`. Muninn refuses records with
  `origin: "principal"` or `by` equal to the principal identifier or to `muninn`.

## In: `POST /api/records`

Body `{"records": [<record>, …]}`. Each record is checked on its own; the reply lists, per record,
`{"id", "status"}` with `status` one of `accepted`, `duplicate` (its `(ext, project, id)` is already held),
`unsupported` (an extension Muninn does not implement: consumed, not acted on) or `invalid` (with `errors`). Posting
again is safe.

| Record | What Muninn does |
|---|---|
| acts `raised` | keeps the task, key = the item's key (the same task a v1 `task` message with that id is) |
| acts `amended` | the task's words and date are the new terms |
| acts `withdrawn` | that project no longer asks: other projects that ask keep it; when none does, it stays open and the user is asked whether to drop it (Acts keep policy) |
| acts `claimed` | evidence: Muninn settles on it (attested, `adopts` the claim), or asks the user first when `origin` is `registrar` (an inference) |
| decisions `raised` / `amended` | the question waits for the user, as it now reads |
| decisions `withdrawn` / `claimed` | the question leaves the user's list (answered in the project, or not needed) |
| `note` | shown to the user with the project's name |
| `related` (`derived_from`) | kept |

## Out: `GET /api/records?project=<name>&after=<cursor>[&limit=<n>]`, `POST /api/records/ack`

`GET` returns `{"records": [...], "cursor": <n>}`: the records for that project after the cursor, oldest first, and
the cursor to read from next. A record is the same on every read (its id derives from what it stands for). A
settlement held for the user's undo window is not returned until it comes due, and one taken back in that window never
is. `POST /api/records/ack` with `{"project", "through": <cursor>}` says the project has them (durably): from then on an
undo is told as a new record (`reopened`, or a settlement that supersedes) instead of being taken back. A reader that
starts on v2 after reading v1 should start from the cursor of its first read, not 0, so as not to receive again what
v1 already delivered.

| What Muninn tells | Record |
|---|---|
| a task done / dropped | acts `settled`, `outcome` `done` / `dropped`, with its authority (below), `terms_rev` the latest the project sent |
| a task repeating one already settled | acts `settled` `already_done`, and `related` `same_as` the settled one |
| a task taken back after it was settled | acts `reopened`, `supersedes` the settlement, `restore_text` its words |
| two projects' tasks taken as one | acts `related` `same_as` (`active: true`) for the merged item |
| the user moved a task's date | acts `note`, `about.key`: what it asks is the project's to amend |
| a fact that bears on a to-do the project never keyed | acts `note`, `about.quote` the to-do in the project's words |
| the user's answer to a question | decisions `settled` `decided` with `answer.text` |
| the user saying a question needs no answer | decisions `settled` `dismissed` with `note` |

Authority of a settlement: the user's own word or tap is `principal` (decider: the principal); the user saying yes to
what Muninn asked is `confirmed` (`evidence.proposal`: `muninn:question:<id>`); a settlement Muninn made on evidence
(the user's words read as settling it, a rule the user set, a project's claim it adopts) is `attested` (decider
`muninn`, `evidence.basis` says what). Muninn does not answer for the user under a mandate, so it emits no `delegated`.

A question asked of a project for the user (v1 `asks`) is outside 0.3 and stays a v1 handoff.

## Privacy, beyond the specification

What a record tells a project is only what that project may hear: content that came from a deep talk with one project,
or from another project, goes back only to where it came from (Muninn's `only_to` / `via`); others hear that something
happened, not why.
