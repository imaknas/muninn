# Muninn ↔ projects, v2: Principal Handoff records

Muninn is the **principal side** of Principal Handoff Core 0.5 with the Acts 0.5 and Decisions 0.5 extensions
(vendored unchanged in `vendor/principal-handoff/`: the schemas, and the records, lifecycle, terms-digest and convert
conformance cases, which Muninn's implementation passes). It implements neither the Evidence nor the Criteria module:
it emits no `evidence.proof`, verifiable entries or approvals, and leaves those it receives to their modules. v2 runs
beside v1 (`messages.v1.schema.json`, `handoffs.v1.schema.json`) until the projects' side reads and writes only v2;
then v1 is retired.

## Versions (core section 14)

Muninn also implements the version before, 0.4 (its schemas in `vendor/principal-handoff-0.4/`), and reads 0.3 as 0.4
(only `ph` and `ext` change). Which version a project is told its records in is configured per project and extension
(`muninn handoff-version`; `*` sets every project, those already named and those to come, for when one counterpart
speaks for them all), not taken from the labels of what it sends. A project first heard from is configured to the
version it spoke then. A record is checked under the rules of the version it
came in, kept as it came, and read in 0.5; one in a version other than the configured one is read all the same and
said in Muninn's log. What Muninn tells a project that speaks 0.4 is converted as core section 14 says: `evidence.proof`
removed, a settlement's `terms_digest` that of the held need record in 0.4, and a relation naming an item of the same
extension names it in 0.4.

## Parties (core R6)

- The principal identifier is configured (`principal_id`); unset, it is `person:<owner_label>` in lower case.
- Muninn's own party identifier is `muninn`. Its records have `origin: "principal"`, `by: "muninn"`.
- The projects' side authenticates with the same bearer token as `/api/messages`. Muninn refuses records with
  `origin: "principal"` or `by` equal to the principal identifier or to `muninn`.

## In: `POST /api/records`

Body `{"records": [<record>, …]}`, read as I-JSON (a member name twice makes that record invalid, alone). Each record
is checked on its own; the reply lists, per record, `{"id", "status"}` with `status` one of `accepted`, `duplicate`
(its `(ext name, project, origin, type, id)` is already held, in whichever version), `unsupported` (an extension Muninn does not implement: consumed,
not acted on) or `invalid` (with `errors`). Posting again is safe.

| Record | What Muninn does |
|---|---|
| acts `raised` | keeps the task, key = the item's key (the same task a v1 `task` message with that id is) |
| acts `amended` | the task's words and date are the new terms |
| acts `withdrawn` | that project no longer asks: other projects that ask keep it; when none does, it stays open and the user is asked whether to drop it (Acts keep policy) |
| acts `withdrawn`, `reason: "replaced"` | it stays open. While its single active `replaced_by` names an act still open, the user is asked about the two together; once that one is settled or withdrawn, or is no longer the single active target, about this one on its own. No answer is put forward, and only the user settles it (never under the standing permission, never from the replacement's settlement) |
| acts `claimed` | the project's word alone settles nothing (core section 7). From an agent: settled at once (undoably) under the user's standing permission for project claims, when he gave one (`delegated`); else he is asked, and his yes adopts the claim (`confirmed`). From the registrar (a line gone or ticked): always asked (Acts section 2) |
| decisions `raised` / `amended` | the question waits for the user, as it now reads |
| decisions `withdrawn` / `claimed` | the question leaves the user's list (answered in the project, or not needed) |
| `note` | shown to the user with the project's name (a registrar's note about a displaced key too) |
| `related` (`derived_from`, `replaced_by`) | kept; `replaced_by` decides how a replaced act is asked about |

A requester that stops listing an item (a handover, a summary) says nothing about it (core R4): only a record changes
an item.

## Out: `GET /api/records?project=<name>&after=<cursor>[&limit=<n>]`, `POST /api/records/ack`

`GET` returns `{"records": [...], "cursor": <n>}`: the records for that project after the cursor, oldest first, and
the cursor to read from next. A record is the same on every read (its id derives from what it stands for), and the
times of Muninn's records only rise, so each settlement or reopening is later than every one Muninn holds for its
item (core section 6; Muninn holds no other principal side's settlements). A settlement held for the user's undo window is not returned until it comes due,
and one taken back in that window never is. `POST /api/records/ack` with `{"project", "through": <cursor>}` says the
project has them (durably): from then on an undo is told as a new record (`reopened`, or a settlement that supersedes)
instead of being taken back. A reader that starts on v2 after reading v1 should start from the cursor of its first
read, not 0, so as not to receive again what v1 already delivered.

| What Muninn tells | Record |
|---|---|
| a task done / dropped | acts `settled`, `outcome` `done` / `dropped`, with its authority (below), `terms_rev` and `terms_digest` of the latest terms the project sent |
| a task repeating one already settled | acts `settled` `already_done`, and `related` `same_as` the settled one |
| a task taken back after it was settled | acts `reopened`, `supersedes` the settlement, `restore_text` its words |
| two projects' tasks taken as one | acts `related` `same_as` (`active: true`) for the merged item; another project's item is named only by a `uri` Muninn alone resolves (`muninn:item:<hash>`) |
| the user moved a task's date | acts `note`, `about.key`: what it asks is the project's to amend |
| a fact that bears on a to-do the project never keyed | acts `note`, `about.quote` the to-do in the project's words |
| the user's answer to a question | decisions `settled` `decided` with `answer.text` |
| the user saying a question needs no answer | decisions `settled` `dismissed` with `note` |

A settlement is bound to the terms it was made against, so one is told only for an item whose need record Muninn
holds; a task that came the v1 way is settled the v1 way.

Authority of a settlement: the user's own word or tap is `principal` (decider: the principal); the user saying yes to
what Muninn asked is `confirmed` (`evidence.proposal`: `muninn:question:<id>`, adopting the claim it was about); a
settlement under the user's standing permission for project claims is `delegated` (decider `muninn`, `under`
`muninn:mandate:<id>`, `chain` from the principal to `muninn`; Muninn keeps the permission, revoked or not); a
settlement Muninn made on evidence apart from any claimant (the user's own words read as settling it) is `attested`
(decider `muninn`, `evidence.basis` says what).

A question asked of a project for the user (v1 `asks`) is outside the specification and stays a v1 handoff.

## Privacy, beyond the specification

What a record tells a project is only what that project may hear: content that came from a deep talk with one project,
or from another project, goes back only to where it came from (Muninn's `only_to` / `via`); others hear that something
happened, not why.
