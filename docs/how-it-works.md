# How Muninn works

[繁體中文](zh-TW/how-it-works.md)

```
sources ──► attribution ──► store ──► drift rules ──► composer ──► channel (Web Push)
(git, Claude Code,   (rules, then        (deterministic,  (dedupe, quiet       │
 plans, ingest API)   local-model         no LLM)          hours, priority     ▼
                      suggestions the                      quotas)        PWA inbox
                      user confirms)
```

- **Domains** are plan files: `data/domains/<id>/plan.yaml` plus a tracker file
  (plan key `tracker`, default `applications.yaml`). Each domain has a
  `priority` (1 is highest) and a `sensitivity_default`.
- **Commitments** are either *goals* (`metric`, `target`, `period: day|week`,
  `priority`, `if_behind: [...]`) or *deadlines* (`due`, `lead_days`,
  `priority`, `done_when: manual` or `{targets: [...], status_at_least: applied}`).
  Tracker status order: `todo < applied < screening < interview < offer`;
  `rejected` and `withdrawn` are closed. `pivot`, `decision_tree` and `filters`
  are human text shown only in the weekly report.
- **Metrics** (`src/muninn/core/metrics.py`): `applications_submitted` (tracker
  dates), `hours` (activity spans tagged with the goal, plus manual
  corrections), `late_activity` (minutes of activity after the goal's `cutoff`),
  `sleep_hours` (longest overnight activity gap, until a real sleep source
  exists), `steps` (reported by the phone through the ingest API). A metric
  declares whether it is *at least* / *at most* and whether it accrues
  (judged pro-rata through the period) or is a single reading.
- **Drift rules** (`core/drift.py`): behind pro-rata, deadline inside
  `lead_days`, overdue next actions, late-night activity, a domain gone quiet.
- **Who may interrupt**: domains with priority ≤ 3 push; lower ones go to the
  evening summary unless a signal escalates; domains without goals are only
  observed. Each priority tier has its own daily push quota. Quiet hours hold
  pushes back, except signals that prove the user is awake (working past the
  cutoff). The same signal is sent once a day unless it gets worse.
- **Reports**: 08:30 morning brief, 22:30 evening summary, weekly report; one
  inbox item each, domains in priority order.
- **Language**: Muninn speaks Traditional Chinese or English (`language: zh-TW | en` in
  config; unset, the machine's locale). The setup screen asks first and the settings page switches it.
  Everything follows it: the screens, what Muninn says, and the language it asks models to write in
  (`muninn/languages/`, `ts/words/`; prompts are in English for both).
- **Getting things ready** (`core/prepare.py`): for open tracker targets due
  soon, Muninn prepares an application packet (match, gaps, resume version,
  draft answers, first-call questions) from the public posting and the user's
  `documents`. For a posting on Greenhouse (with the `autofill` extra installed) it also fills in the job's application
  form in a headless browser (`core/apply.py`): a model answers each field from the packet, the résumé and what the
  user answered on earlier forms, Muninn asks in the conversation only what the user alone can say, and the filled form
  (each field, and a picture of it) waits for one press to send. Nothing goes out without that press; a CAPTCHA or a
  code sent to the user's mail is left to them. Elsewhere the user applies and taps "submitted". Everything Muninn
  does on its own is listed with an undo.

### Activity sources (metadata only)

- **git**: commit time, repo directory name, per-extension file/line counts,
  and whether a `Co-Authored-By` trailer names an AI assistant (those count as
  `agent_commit`, not as hands-on time). No messages, paths or diffs.
- **Coding-agent sessions** (Claude Code in `~/.claude/projects`, Codex in
  `~/.codex/sessions`; which agent a file is from is read from its first line):
  timestamps, working directory and session id; only *attended* activity
  counts (a human prompt and the 10 minutes after it), so an agent running on
  its own is not the user's time. A Codex session counts only when it was
  started in Codex's own command line or editor client, not by another program
  (`codex exec`, a custom client) or as a sub-agent. No message text.
- **Computer activity** (`src/muninn/contracts/activity.v1.md`): what a collector on a
  computer sends; the Mac collector (`src/muninn/collectors/mac`, installed with
  the line on the settings page) sends ActivityWatch
  window activity while not AFK (Chrome windows overlapping the browser
  extension, installed only in the work profile, carry
  `browser_profile=work`), the laptop's commit metadata, and the work Claude
  transcripts synced for the transcript source.
- **Reflection** (`core/reflect.py`): conversation text with the assistant
  (the user's messages and the assistant's written replies; never tool
  input/output or hidden reasoning; only sessions the user typed into) is
  digested nightly per session (summary, commitments,
  decisions, themes, mood, friction). The Monday reflection (on the last complete week) counts those in
  code and has a model write a short reflection. Under privacy routing the
  local model does both, and conversation text never goes to a cloud model;
  under `routing: cloud` the cloud model does (see [the privacy model](privacy.md)). Projects that keep their own records
  (`external_todos`, `project_states`, `weekly_stats`) are the authority for
  project to-dos and decisions: the reflection keeps to personal commitments,
  mood and friction, cites project decisions from those records, and the
  user's project to-dos are shown, not re-extracted.
- **Ingest API**: `POST /api/ingest` with `Authorization: Bearer <data/secrets/ingest_token>`
  and `{"observations": [{source, key, ts, kind, payload, domain?, goal?, sensitivity?}]}`.
  External observations default to `sensitive`. New observations are
  evaluated immediately.
- **Project messages**: `POST /api/messages` (same bearer token) with
  `{"messages": [{id, project, type: question|progress|done, text, handoff_id?}]}`,
  from the system that keeps the project records. `id` is the sender's and
  makes retries safe. A question waits for the user in the panel; the user's
  answer leaves as a handoff to that project with `reply_to` set to the
  question's id. Progress and done go to the inbox; `done` with a
  `handoff_id` marks that handoff done.
- **Project records** (`src/muninn/contracts/records.v2.md`): the same
  exchange as Principal Handoff 0.5 records (0.4 still read and spoken to a
  project configured for it), `POST /api/records` in and
  `GET /api/records` out. Muninn is the principal side: only the user settles
  a to-do or a question. A project saying something is done settles nothing by
  itself: the user is asked, unless he gave Muninn a standing permission to
  take a project session's word (undoably); a line gone from a project's state
  file is always asked about. A project that stops listing a to-do leaves it as
  it is.

### Acting on the user's behalf

Three tiers: done and undoable (blocks in Muninn's own calendar, status set
from an application confirmation email, packets prepared); ready for one tap
(application forms filled in, sent on that tap, and never sent a second time when the site did not say it got
the first; reply drafts saved to the mailbox, status changes from interview/offer/rejection emails); never (sending mail, answering other people's invites,
paying). Accounts connect with `muninn connect google|microsoft` or the
PWA's "連結" buttons: web OAuth clients whose redirect is the PWA's
`/oauth/<provider>/callback`, checked by a single-use state. Google: Gmail
read + compose (drafts), Calendar read + events (writes only to the Muninn
calendar). Microsoft 365 (work): Mail.Read and Calendars.Read only; work
busy time is merged into scheduling. Personal mail may go to the cloud model
only with `cloud_mail: true`; work mail is local-only (under privacy routing; see [the privacy model](privacy.md)).

## Interfaces other systems use

Other systems rely on interfaces published in `src/muninn/contracts/`, each with a
version: `muninn handoffs --json`, `muninn digests --json`, `muninn tasks --json`
and `POST /api/messages` (v1), the project records (`records.v2.md`, Principal
Handoff 0.5), the permission requests of a deep talk (v2), what a computer's
collector sends (`activity.v1.md`) and where the user is (`location.v1.md`).
Adding a field keeps the version; renaming one or changing its meaning is a
new version, served alongside the old one until readers move.
`tests/test_contracts.py` checks the real output against the schemas.
