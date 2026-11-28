# Muninn as a Principal Handoff Transport server (experimental)

Muninn is the server of Principal Handoff Transport 0.1, an experimental companion of the core (the
specification's `transport/SPEC.md`, not released; no conformance suite or reference server yet). It carries
the same records as records.v2 (`records.v2.md`), which runs beside it, unchanged, until the projects' side speaks
the transport; then records.v2 is retired.

## Base URLs

- Over the tailnet: `<public_url>/ph/`, through `tailscale serve`, which gives HTTPS (its certificate names the
  tailnet address). The server checks `Host` against it.

Plain HTTP to the loopback port is not a base URL (it authenticates no server): a request there names another `Host`
and is answered `421` `misdirected`. No CORS headers are sent.

## Credentials and parties

- A client presents `Authorization: Bearer <token>`. The token is the same as for records.v2 (`ingest_token` in
  Muninn's secrets): a credential for a platform that relays for its projects' agents and registrar. It speaks for
  every party the role configuration gives a role, so a fetch or an acknowledgement names its party with `as`.
- The role configuration: every party but the principal and Muninn (`muninn`) holds the requester, agent and
  registrar roles in every project. Muninn is the only implementation acting as the principal side, so a record whose
  `origin` is `principal`, or whose `by` is the principal or `muninn`, is `not_authorised`.

## What the log holds

Each project's log holds every record Muninn stored about it (answered `accepted` or `kept`), as it was sent, and every
record Muninn makes about it, in its 0.6 form, converted when returned to the version the project is configured for
(`muninn handoff-version`). A fetch returns records of every origin, the client's own included.

- **A settlement is sent once it is in the log.** One held for the user's undo window (a close Muninn inferred)
  enters the log when the window ends; one taken back in it never does. A settlement the user made himself is in the
  log as he makes it, so his undo after that is a new record (`reopened`, or a settlement that supersedes it). An
  acknowledgement changes none of this: it only says where the party resumes.
- What a project sends again with other content under a used id is `accepted` and appended; which form every reader
  keeps is core section 10's.
- A record of a version or extension Muninn does not implement is `kept` (`reason: not_implemented`) if it is I-JSON
  with string `project`, `ext`, `origin`, `id` and `by`; a valid 0.5 record with no 0.6 form is `kept`
  (`reason: no_form`).
- When the log was first made, the records Muninn held then entered it, in the order they came or were made. Earlier
  forms of an id a project reused, and handoffs older than Muninn keeps (60 days after their last change), are not in
  it.

## Cursors and generations

A cursor is `g<generation>.<position>` and opaque to clients. After restoring an older copy of Muninn's database, run
`muninn records-log --new-generation`: every earlier cursor becomes `unknown_cursor` and the acknowledgements are
dropped, so clients read from the beginning and send again what the log no longer has.

## Limits

At most 500 records per send, 256 KiB per record and 4 MiB per body; at most 200 records per answer (`limit` lowers
it); `wait` up to 60 seconds. There is no rate limit per project yet.
