# Muninn

English · [繁體中文](README.zh-TW.md)

Always-on personal assistant: collects observations, tracks commitments per
life domain, detects drift from the plan, and pushes short nudges with concrete
corrective actions through a self-hosted PWA.

Runtime data (plans, observations, keys, state) lives in `data/` and is never
committed.

## How it works

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
- **Claude Code transcripts**: timestamps, working directory and session id;
  only *attended* activity counts (a human prompt and the 10 minutes after
  it), so an agent running on its own is not the user's time. No message text.
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
  under `routing: cloud` the cloud model does (see the privacy model below). Projects that keep their own records
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
  exchange as Principal Handoff 0.4 records, `POST /api/records` in and
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
only with `cloud_mail: true`; work mail is local-only (under privacy routing; see below).

## Privacy model

Where models run is one setting, `llm.routing`:

- `cloud`: every request goes to the cloud model (the `claude` CLI, or the
  Anthropic API with your own key: see `llm.providers` below), work
  and unattributed material included, and no local model answers, not even
  when the cloud fails (Muninn says so and keeps the sentence as a note): a
  local model takes over a task only once `muninn eval` shows it can. The
  rest of this section then describes what is *kept* locally and labelled,
  not what is withheld from the cloud.
- `privacy` (the default in config): the rules below decide what may leave.
  It needs a local model for what must stay on the machine, so the setup page
  writes `cloud` when you set up a cloud provider's key, and says so on that
  screen; the settings page always says what goes where.

Which models answer is `llm.providers`:
each provider names its implementation (`claude` for the CLI and its login,
`anthropic` for the Anthropic API, `openai` for any service with an OpenAI-style
Chat Completions API at its `base_url`, such as OpenRouter or LM Studio, and
`ollama`), where its key is (`{secret: <file in the secrets
directory>}` or `{env: <VARIABLE>}`, never the key itself) and a model per tier.
One provider may be `location: local` (Ollama's default; its `base_url` must be
on this machine or network): it is the local model that privacy routing keeps
sensitive content on. For the cloud providers,
`llm.use` says which provider serves the `fast` tier (everything but your own
words) and the `standard` one (routing, facts, answers). A key that is missing
or rejected is a lapsed login: Muninn says so, and no other model answers in
its place. A config without `providers` keeps using the CLI as before.

To cap what the cloud models cost, set `budget.daily_usd` and give each model
in use its price (`llm.providers.<name>.prices: {<model>: {input: 3, output:
15}}`, US dollars per million tokens). Every reply counts, including the checks
Muninn runs on its own answers and the `muninn eval` runs you start yourself
(those run to the end even past the limit). Once a day's calls reach the limit, no
cloud model is asked until the next day: what you say is kept as a note, other
work waits, and Muninn tells you once. A model whose replies don't say how many
tokens they took (the Claude CLI) or that has no price can't be counted, so
`muninn check` reports it as a problem instead of letting it run past the limit,
and the commands that ask models don't run until it is fixed.

Either way every request and reply is stored on the machine as an
evaluation case, with the user's corrections (undone actions, cancelled
handoffs, answers to Muninn's questions, window labels), so `muninn eval
--backend <provider:model>` can show per task whether another model (a local
one, a cheaper one) could take over. Before there are any cases, `muninn eval
--suite` runs a small set of invented ones shipped with Muninn (what a sentence
asks for, what news settles, whether a sentence is about work) on the models
the tasks use, or on `--backend`, in each language there are cases for (or
`--language`), and says per language how often each was right, how many replies
were off their declared shape, and how many replies to the user were not in that
language. Claude Sonnet 5.5 and Haiku 5.5
answer every one of them right.

- Unattributed observations, and anything attributed to a domain listed in
  `sensitive_domains`, are **sensitive**. Sensitivity is never lowered.
- `core/llm_router.py` is the only path to an LLM. Sensitive or unattributed
  content goes to the local Ollama model only; if it is down, the request is
  skipped, never sent to the cloud.
- The cloud model only ever receives a `CloudSafe` payload built in
  `core/privacy.py` from whitelisted fields (titles, next actions, progress
  numbers), used for the evening summary wording, and — only with
  `cloud_drafts: true` — application packets built from the public posting
  and the user's own documents (never plan notes, pivot, decision tree or
  filters). Goals or deadlines marked `private: true` are left out.
  `tests/test_privacy.py` and `tests/test_prepare.py` prove this.
- Conversation digests (the local model's per-session summary, themes,
  mood, commitments, decisions and friction) are sensitive like the text
  they come from. They leave Muninn one way only: `muninn digests --week
  YYYY-Www --json`, which the user runs (or schedules) for their own weekly
  reflection elsewhere. It prints the digests' listed fields, never
  conversation text, and withholds every session whose domain is in
  `sensitive_domains` or unknown (by today's attribution of its project, or
  by the digest), giving only their count.
- Push notifications name the thing ("每週投遞落後:本週 0/4"); payloads are
  encrypted end to end (RFC 8291), so the exposure is the lock screen, which
  the phone's preview setting governs. Sensitive items (work, unattributed)
  only say `"<domain>: N new reminders"`; `push_detail: counts` makes every
  push say only that.
- The PWA requires a paired device (one-time code from `muninn pair`, then an
  HttpOnly cookie), optionally restricted further to listed Tailscale logins.

## Layout

```
src/muninn/core/       domain-agnostic logic; depends only on core/ports.py
src/muninn/adapters/   store (SQLite), plans (YAML), sources, LLM backends,
                       Web Push channel, web app + PWA
src/muninn/adapters/web/ts/   the PWA in TypeScript (strict), compiled into web/static/
                       by `npm run build`; the compiled .js is not committed
src/muninn/main.py     composition root and CLI (the only place adapters are chosen)
src/muninn/contracts/  the interfaces other systems read or post, and the formats Muninn reads
                       (schemas, model decision points), shipped inside the package
examples/              config.yaml and attribution.yaml templates for data/
```

Storage is a set of small ports in `core/ports.py`, one per kind of record
(observations, inbox, tasks, handoffs, leases, …). A module takes only the
ones it uses, so another backend can be written port by port; `Store` is all
of them together, and only the composition root and the engine take it
(`tests/test_architecture.py` checks this).

Other systems rely on interfaces published in `src/muninn/contracts/`, each with a
version: `muninn handoffs --json`, `muninn digests --json`, `muninn tasks --json`
and `POST /api/messages` (v1), the project records (`records.v2.md`, Principal
Handoff 0.4), the permission requests of a deep talk (v2), what a computer's
collector sends (`activity.v1.md`) and where the user is (`location.v1.md`).
Adding a field keeps the version; renaming one or changing its meaning is a
new version, served alongside the old one until readers move.
`tests/test_contracts.py` checks the real output against the schemas.

## Installing

On macOS or Linux, one line installs Muninn, starts it in the background (it
runs its own schedule), and opens its setup page: pick a model, paste its key,
scan the QR code with your phone.

```bash
curl -fsSL https://raw.githubusercontent.com/imaknas/muninn/main/install.sh | sh
```

Or, with [uv](https://docs.astral.sh/uv/): `uv tool install muninn-ai`, then
`muninn init` and `muninn serve`, and open the setup link it prints (the
package is muninn-ai; the command is `muninn`).

To move to a newer release, run `muninn upgrade`: it installs the latest
stable one, restarts the background service on it and checks that it answers,
and puts the version before back if it does not (`muninn upgrade --check` only
says what is installed and what is out). In Docker, pull the new image instead.

On a NAS or a server, `docker compose up -d` (see `compose.yaml`); the setup
link is in `docker compose logs`. Either way your phone reaches Muninn over
HTTPS: Tailscale (`tailscale serve`) is the shortest way there. `muninn backup
DIR` copies the database, plans and settings; `muninn check` says what in the
settings cannot work as written.

## Running from a checkout

```bash
uv sync                            # --extra autofill to fill in application forms (then: uv run playwright install chromium)
npm ci && npm run build            # the PWA's TypeScript → web/static/*.js (type-checked)
uv run pytest
uv run muninn init                 # VAPID keys + ingest token in data/secrets/
uv run muninn --dry-run tick       # collect + evaluate, print pushes instead of sending
uv run muninn morning | evening | weekly
uv run muninn serve                # PWA + API on 127.0.0.1:18795
uv run muninn pair                 # one-time pairing code for a new device
uv run muninn devices [--revoke ID]
```

`MUNINN_DATA` points at the data directory (default `./data`); copy the files
in `examples/` there. Every CLI entry point is idempotent. Run this way,
nothing runs on a schedule unless the config says `jobs: default` (the
installer and the container write it): `serve` then runs tick, the morning and
evening reports and the nightly reflection itself.

## Testing

```bash
uv run pytest -n 4                 # unit and API tests, 4 at a time (fast; what a merge runs)
uv run playwright install webkit chromium   # once per machine, for the browser tests (Chromium fills forms)
uv run pytest -m e2e -n 8          # the PWA in WebKit, as an iPhone and as a laptop, against the real app over HTTPS
```

The browser tests compile the TypeScript first (`npm run build`), use the unit tests' fake models, clock and invented data,
block service workers so the shipped static files run, and fail on any page or
console error. Safe-area insets (the iPhone notch) cannot be emulated, so the
layout checks are approximate.

## Deploying

`bin/deploy.sh` pins the commit it starts from, compiles the TypeScript and
runs the unit and browser tests (a type error or a failing test stops it with
nothing changed); where a platform test runner is installed (`agenthub test`),
it hands that commit to it instead (`MUNINN_TESTS` chooses). The runtime clone (`MUNINN_RUNTIME`, default
`~/services/muninn`) then takes that commit straight from this checkout, runs
`uv sync` (with the `autofill` extra, and Chromium for it) and `npm ci && npm run build`, restarts the launchd job and runs
`bin/smoke.py`: the running server checks its own dependencies on `/api/smoke`
(store, each cloud model in use, the local model, Google, the session runner, the form-filling browser;
no model is asked anything) and the served app must be the one in the repo.
Only then is `main` pushed. A version that fails after the restart is rolled
back on its own, and the runtime's `data/deploys.log` records every deploy and
why one was rolled back; the script's header lists its exit codes. One deploy
runs at a time. The server is
exposed to the tailnet only, with `tailscale serve --https=8444`. First-time
setup on the phone: [docs/d1-checklist.md](docs/d1-checklist.md).
