# Muninn open-source plan

Status: draft, second version (evening of 2026-10-29).

- **First version**: drafted, then revised after a blind review.
- **This version**:
  - Folds in what went live that day: Conventional Commits, the location interface, API key providers, deploys that roll back automatically.
  - Catches up with version two of agent-hub's provider design.
  - Line numbers in §1 re-checked against main (d488f16).
- **All of §7 is decided** (evening of 10-29, on the defaults).

## Ten-line summary for the owner

1. **Goal**: someone with nothing but an API key can install Muninn and talk to it, without Claude Code, without the private platform, and without a Mac. The key can be Anthropic, OpenAI, OpenRouter, or a local Ollama or LM Studio.
2. **New installs without a model**: someone who keeps the defaults and has no local model currently gets "no model responded" for almost everything they say. Instead, a new install starts in "no model" mode and runs only rule reminders; Muninn starts understanding what you say only once a provider is set up.
3. **Models**: the config shape already matches the platform's and is live (`llm.providers` + `use`); an Anthropic API key works today. Still to do for v1:
   - two implementations: openai (OpenAI-compatible: covers OpenRouter, Gemini's compatible endpoint, LM Studio) and native ollama (including image reading);
   - structured-output validation, thinking effort, token accounting.
4. **When photos can't be read**: Muninn says plainly whether "this model can't read photos" or "the settings only let the local model see photos".
5. **Deep talks and sandboxes**: these need an agent runtime, which v1 does not ship. The runner contract is already independent of which agent is used; when the environment can't open a deep talk, Muninn states the reason the runner gives (d488f16).
6. **Personal values**: before going public, remove your personal defaults, name, project names, host names and paths from code and tests. The method: first write your values into your config file, then change the defaults, and compare the effective config before and after to prove nothing changed on your side.
7. **Public repo**: start from a clean tree with git archive; history is a single commit (decided). agent-memory and local working files are never pushed.
8. **Two repos**: the public repo is the only upstream for code. Your config, launchd and deploy records go in a separate private repo. The changelog is no longer hand-written; it is generated from commit messages at release time (live).
9. **Database upgrades**: additive only, with an automatic backup before upgrading; a failed deploy rolls back to the previous version automatically (live).
10. **The license is Apache-2.0** (decided 10-29), covering Muninn, agent-hub and the contracts the two share. The plan has 9 steps; after each one you can keep using Muninn every day as usual.

## 1. Inventory: where Muninn is tied to the owner's environment

Line numbers are as of main at d488f16.

### 1.1 Model access

**Routing**
- `PrivacyFirst` blocks content that is sensitive, unattributed, or belongs to the work domain (src/muninn/core/llm_router.py:69-78). `CloudFirst` sends everything to the cloud and does not let the local model answer when the cloud fails (:81-90).
- `llm.routing` defaults to `"privacy"` (src/muninn/config.py:70).
- `cloud_first = routing == "cloud" and cloud` (src/muninn/main.py:147): with routing: cloud set but no cloud, it **silently falls back to privacy**, and the settings page still shows privacy (main.py:404-405).

**Providers** (after 4e1205b)
- Implementations are registered in `IMPLS = {claude, anthropic}` (main.py:369); configuration lives in src/muninn/llm_config.py.
- The default `llm.cloud: "claude-cli"` (config.py:69) is read through `LEGACY_CLOUD` as the claude provider (llm_config.py:34, :96-105), so new installs still go through the Claude CLI.
- The `Implementation` Protocol declares only `label` and `default_models` (llm_config.py:37-39); `from_spec` and `backend()` happen to exist on both implementations but are not declared.
- smoke uses `hasattr(b, "version")` to tell cloud from local (main.py:343).

**Model names**
- The CLI uses aliases (claude_cli.py:94, :191); the API uses full ids (anthropic_api.py:78).
- The 6 task groups each have a tier (core/task_models.py:28-38). Tasks outside a group use the router's own model (task_models.py:52-55, main.py:158-161).

**Claude CLI backend**
- Login: a headless token (claude_cli.py:52-65, variable name at :38), or the keychain (:68-72).
- Finding the binary: under nvm (:75-86, `NVM_DIR` or `~/.nvm`, :208).
- Keeping outside config out: SEALED keeps the user's MCP, hooks and memory out (:41-43).
- Environment: the subprocess gets the whole environment except the key variables (:117).
- smoke: runs the CLI once every time to get the version.

**Reading files**
- Whether a backend can read files is a single boolean, `reads_files` (claude_cli.py:133, anthropic_api.py:29); ports.py:116 mentions it only in a docstring, and the router reads it with getattr (llm_router.py:159).
- Photos and PDFs are passed as Anthropic-format content blocks, shared by the CLI and the API (adapters/llm/content.py:11-23); text files are inlined as text (:20-21).
- The Ollama backend accepts no attachments (ollama.py:26).
- With attachments, the router keeps only the backends that can read files (llm_router.py:158-159; tests/test_uploads.py:41-47). When filtering leaves none, it only writes an info log (:177), and the user sees "no reply, or couldn't connect" (commands.py:264-267).
- Under privacy, attachments are never let out (understanding.py:91), so photos only go to the local model, which can't read images.

**JSON**
- There is only one flag, json_mode: Ollama writes it into the prompt (ollama.py:34-36); neither the CLI nor the API uses it (claude_cli.py:135-165, anthropic_api.py:56-70).
- core has 15 places that tolerate bad output with `find("{")`, spread over 14 files.
- There are 18 output schemas (contracts/llm/); the 22 decision points are in contracts/llm-points.yaml:21-280.

**Failures and lost login**
- `stood_in` only means the local model stood in for the cloud (llm_router.py:178).
- `CloudLoginLost` (ports.py:101) is the only error where no stand-in is allowed (llm_router.py:169-173). It has three sources:
  - CLI output matching the lost-login wording (claude_cli.py:32-35, :154-164);
  - the API returning 401/403 (anthropic_api.py:52-53, :64-65);
  - a missing key (keys.py:36, :49).
- On a lost login a push notification goes out (assembly.py:641-647); each backend supplies its own fix hint (claude_cli.py:92, anthropic_api.py:35).

**The local backend is always built**
- The local backend built at main.py:150 is always in the router (:166); reflection builds another one (:171).
- The default qwen3.6:35b-mlx (config.py:65) runs only on Apple silicon.
- Under privacy, WorkCheck goes only to the local model (understanding.py:52), and its route is local_only (llm-points.yaml:27). So someone with only a cloud key gets almost nothing read with the default settings.

**Evaluation**
- The eval candidate is hardcoded to Ollama (main.py:568); the ModelJudge that scores is at main.py:570.
- reference() only uses cloud replies as references (evals.py:268-269); non-local candidates only get cloud_ok cases (:311).
- `--prompt-check` (main.py:535-553) calls the provider's backend directly, bypassing the router, and does not filter on cloud_ok (:551, store_sqlite.py:557-563). It refuses to run when there is no cloud (:537-539).
- New users have no eval cases (the case recorder is at main.py:149).

### 1.2 Features that need Claude Code or the private platform

**Deep talks**
- core knows only the `SessionRunner` port (lease.py:44, :87; ports.py:789-825).
- The only implementation is HttpSessionRunner (lease_runner.py:1-3, :17, :24-35). It uses the lease-runner v2 contract, which states that it is independent of the agent (vendor schema :5).
- It is enabled only when configured (config.py:51-52, main.py:279-292).

**Sandboxes**
- HttpSandboxes is built at main.py:312-314.
- Promoting a sandbox to a project, or renaming it, creates or changes a GitHub repo (lease_runner.py:151, :173).
- LocalProjects can create and promote (local_projects.py:168, :185), but has no deep talks.

**Project records**
- The default is external (config.py:55): FileProjectRecords reads the platform's external_todos, project_states and weekly_stats (main.py:310-311).
- test_local_projects.py:89 asserts this default.
- External contracts:
  - `handoffs --json` (main.py:594-606);
  - POST /api/messages (README.md:79-85);
  - contracts/*.v1;
  - Principal Handoff 0.3 (vendored, Apache-2.0, with LICENSE and NOTICE).
- Principal Handoff files are read by repo path (main.py:274, assembly.py:168).

**Two tools that fail silently without a runner**
- ask_project: offered whenever `answered` exists (project_messages.py:91), and main.py:236 always supplies it.
- talk_to_project: always offered (tools/conversation.py:222-223), and says "connected to X" (:206); without a runner, lend returns nothing (app.py:344-345).

**The "is it live" check** (new)
- Reads the repo's git version and the runtime's data/deploys.log (adapters/running_version.py, main.py:88-95, :275).
- requests.py:30 parses the rollback record format that deploy.sh writes; app.py:61, :903-918, :1241 check on a timer.
- On an install without git, or one that doesn't use bin/deploy.sh, nothing is ever marked as live.

**Other**
- command_trust's rules are written for macOS (command_trust.py:18-19, :27).
- ClaudeCodeSource is always built (main.py:191) and reads ~/.claude/projects (config.py:61); if the directory doesn't exist it is skipped (claude_code.py:99-100); reflection reads it too (main.py:259).
- A copy of the platform contracts is in contracts/vendor/records/ (synced from agent-hub, checked by the platform every night).
- **agent-hub's name appears in code and docs**:
  - Where: llm_config.py:3, :9, :137, config.py:67, examples/config.yaml:51, README.md:114.
  - The wording is "the config shape is the same as agent-hub's".
  - agent-hub will also be open-sourced, so naming it is fine; but if it goes public after Muninn, the wording has to make sense without it.

### 1.3 Machine and operating system

**Deploy**
- The runtime defaults to `~/services/muninn` (deploy.sh:41).
- Restart: through launchd (:128-134, :197), label `local.muninn` (:42); `MUNINN_RESTART_CMD` can replace it.
- uv: falls back to `~/.local/bin/uv` when not found (:48).
- Lock: flock or lockf (:77-90).
- Flow: the runtime fetches the given commit from the deploying side (:183-190); the push happens only after smoke passes (:215-221); a failure rolls back automatically (:167-181, recorded in data/deploys.log).
- smoke.py:38 hardcodes the ingest_token path.
- The deploy instructions in README.md:214-222 still describe the old flow (push, then pull; manual rollback) and need updating.

**Laptop agent (macOS only)**
- Uses LaunchAgents (install.sh:14, :65-82) and osascript (:36).
- Uses ssh to read the token on the server (:39-40), and scp to fetch files from the server's services/muninn (:4).
- Transcripts are copied to a fixed folder on the server (:58-59).
- nested_roots defaults to the name of the work folder (install.sh:56, muninn_agent.py:221); the work Chrome profile is hardcoded (install.sh:86-100).

**Scheduling**
- config.py:34 says "the scheduler owns real time", but the repo has no scheduler for tick, reflection, or the morning and evening briefs.
- The only one is the in-server live check every 15 minutes (app.py:61, :1241).

**Access**
- Binds only to 127.0.0.1 (config.py:28, main.py:678); outside access relies on tailscale serve (app.py:4-8).
- The allowlist only looks at the tailscale-user-login header (app.py:128-134), which can be forged by anyone not going through tailscale serve.
- The pairing cookie is secure=True (app.py:695): **without HTTPS, pairing is impossible**.

**Phone, accounts, packaging, data**
- **Location**: goes through the `LocationApp` interface (ports.py:157, core/location.py). OwnTracks is the first implementation (main.py:84); the old path is kept at app.py:1197.
- **Health data and Focus modes**: through iOS Shortcuts (health.py:1, app.py:741-751, app.py:1200).
- **OAuth**: the user has to create their own Google client (oauth.py:11-22, main.py:212) and Microsoft client (graph.py:22-29, main.py:213). Microsoft only accepts work or school accounts (`/organizations/` at graph.py:24-25).
- **Default mail accounts**: personal Gmail plus work Outlook (config.py:39-42), and the settings page hardcodes these two names too (pages.ts:198).
- **Default workspace_roots**: `~/workspace` (config.py:60), which GitSource scans (main.py:188-190).
- **Packaging**:
  - Needs Python >=3.13 (pyproject.toml:6); the new hard dependency is `anthropic>=2.3.0` (:8).
  - uv_build ignores .gitignore, so the JS has to be built before packaging.
  - contracts are read by repo path (main.py:274, :524).
- **Data directory and .env**:
  - The data directory defaults to ./data (config.py:114).
  - .env.example tells people to copy it to .env, but the code never reads .env.
  - git_authors reads the global git config (config.py:171-178).
- **Backup**: relies on the platform's nightly encrypted offsite backup; nothing about it is in the repo.

**Migrations**
- Every process that opens SqliteStore adds missing columns (store_sqlite.py:136-146, :849-853) and runs four data migrations (:147-150). There is no version number and no cross-process lock.
- DeviceRegistry touches the same DB file over its own connection (devices.py:41-46, main.py:297).

### 1.4 Personal data and hygiene

**Personal values in code**
- **Name**: owner_label defaults to the owner's name (config.py:44, assembly.py:119, engine.py:78); principal_id, when unset, is `person:<owner>` (handoff_records.py:231-233).
- **talk.ts:621 strips a prefix**: this line strips the 「問…:」 ("Ask …:") prefix, carrying the owner's name, that the runner sends. It has to be generated from owner_label, not simply changed to a generic word.
- **Host name and employer**: the host name is in app.ts:112 and pages.ts:322, the employer in pages.ts:319. The same host name also appears in devices.py:3, docs/design/decision-models.md:70, d1-checklist.md, and docs/archive/.
- **Work folder and Chrome profile**: muninn_agent.py:134, :207, :221, install.sh:56, :86-100.
- **Time zone**: the owner's time zone is hardcoded (config.py:27, examples/config.yaml:4).

**Prompts and tests**
- **A real project name in prompts**: one real project name appears in ideas.py:115, :154, tools/ideas.py:33 (it goes into the route prompt, line 32 of the golden file), and topics.py:143.
- **The owner's name in tests**: test_project_records.py:62, :74, :75, test_project_side_contract.py:81.
- **Real project names in tests**:
  - test_idea_next.py:125-130, :185-188;
  - test_sidecar.py:163-170;
  - tests/e2e/test_flows.py:559-561;
  - test_handoffs.py:112-114;
  - test_ideas.py:224-234;
  - test_lease_runner_sandboxes.py:76-82.

**Docs**
- conversation-first.md:184 has a personal website.
- d1-checklist.md:13, :31 and README.md:217 have local paths.
- .env.example:4 has a host name; examples/config.yaml:7 is already a placeholder.

**changelog**
- CHANGELOG.md has been deleted (1c4148e) and replaced by Conventional Commits plus cliff.toml; deploy.sh no longer commits either.
- The old content survives only in history (314 commits touched it); once history is reset it won't be public.

**History and working directory**
- **Commits**: 516, all with the owner's personal email; 515 of them carry Co-Authored-By.
- **Old commits that need clearing**:
  - e9aa5bc once committed a node_modules link pointing to /Users/…; bec5128 removed it;
  - 0ba8d40 spelled out the private platform's name; a42be0b removed it;
  - the .claude/worktrees gitlink was added in 8bcd222 and removed in 266df1f.
  - Resetting history keeps all of these out.
- **Working directory**:
  - About 164 local branches.
  - `.agent-memory/` is listed only in .git/info/exclude, not in .gitignore.
  - `.claude/` ignores only worktrees and settings.local.json.

**License and secrets**
- The root LICENSE is Apache-2.0 (10-29) and covers vendor/records; principal-handoff carries its own LICENSE and NOTICE.
- gitleaks has not been run yet.

## 2. Model providers

### 2.1 Already live (4e1205b, d488f16)

```yaml
llm:
  providers:
    anthropic: {impl: anthropic, key: {secret: anthropic-api-key},
                models: {fast: claude-haiku-5-5, standard: claude-sonnet-5-5, strong: claude-opus-5-5}}
    claude:    {impl: claude}
  use: {fast: claude, standard: anthropic}   # can be omitted when there is only one provider
  models: {mail: standard, reflect: {provider: anthropic, model: claude-haiku-5-5}}   # overrides for individual task groups
```

- **Config shape**: the same as the platform's `llm.providers` (src/muninn/llm_config.py). `impl` now has `claude` (the Claude CLI) and `anthropic` (the Anthropic API, src/muninn/adapters/llm/anthropic_api.py). A model is written as a string or `{name, context}`; tiers left out use that provider's defaults.
- **Tiers and task groups**:
  - Muninn's 6 task groups each map to a tier: route, facts and answer use standard; draft, mail and reflect use fast (core/task_models.py).
  - The 13 decision points without a group use the router's own model: the main router is fast, the understanding router is standard.
  - `llm.models` can move a group to another tier, to another model from the same provider, or to `{provider, model}`.
- **Where the key lives**:
  - `{secret: <file name>}` is a file in the secrets folder, and is the default; `{env: <variable>}` is an option.
  - A key written directly into the config stops startup, and the error message does not echo what was written.
  - The key is read on every request, so changing it needs no restart.
- **On errors**:
  - A missing key and a rejected key (401/403) both count as a lost login (CloudLoginLost): no other model may answer instead, and the user is told where the key should go.
  - A reply truncated at max_tokens, or a model refusal, counts as a failure.
- **No leaking**: environment variables declared as a provider's key are not passed to the claude CLI subprocess.
- **Deploy check**: smoke asks the API for model info (models.retrieve) once for each model in use; this costs no tokens.
- **Old configs still read**: a config without `providers` (llm.cloud, claude_binary, cloud_model, understanding_model) reads into the same shape; not one character of the owner's config changed.
- **Decision point registry**: all 22 points state `shape: completion` (one call, no tools).

### 2.2 Aligning with the platform

Based on version two of agent-hub's provider design (10-29) and its reply of 10-29 20:47.

| Item | Platform | Muninn now | To do for v1 |
|---|---|---|---|
| impl names | claude, anthropic, openai (Chat Completions-compatible + base_url), ollama (native API) | claude, anthropic | Add openai and ollama; use the platform's names, not openai-compatible |
| Tiers | fast / standard / strong | Same | — |
| Per-decision-point override | `llm.points.<point>: {provider, model}` | Only `llm.models` (per task group) | Add `llm.points` with the same name and shape; a point's setting overrides its group's |
| Model context | `{name, context}`, length checked before sending | Read but unused | Don't send when over the context; the failure reason says "prompt too long" |
| Structured output | Use the provider's schema mode where available; strip `<think>`; validate against the schema; on a failure, retry once with the error attached; no retry on truncation | One json_mode flag, plus 15 places in core that tolerate bad output with `find("{")` | Follow the platform's approach, in one place in the router, validating with contracts/llm/*.schema.json; the 15 tolerant spots collapse into one ✅ |
| Keys | File is the default, env an option; set-key reads from stdin; doctor checks in the same environment as the scheduler | Both file and env; no set-key | A key pasted on the settings page is written to secrets (600); `muninn check` checks env keys in the service's environment |
| Redaction before sending | Everything sent to any provider passes one redaction point that masks credential-like strings | None | The router redacts before sending to the cloud |
| Local | `location: local` allows only loopback, private ranges and the tailnet | ✅ Same (plus host.docker.internal); at most one | `use`, `llm.models` and `llm.points` can point only at cloud providers: the local provider is only for privacy routing (the platform's points can point at the local provider; this differs on purpose, so the same backend never counts as both cloud and local in routing) |
| Cost | Token accounting; optional `budget.nightly_usd` | Not recorded | Record the token count of every call (in the call log eval already keeps); optional `budget.daily_usd` (Muninn runs all day, so it is per day, unlike the platform's nightly budget); when exceeded, stop according to each point's on_failure and push once ✅ (10-31: core/budget.py; prices per model under `llm.providers.<p>.prices`, no built-in price table; over the limit no cloud model is asked, a local one still answers what it may; every point, the user's own words included, stops as on its failure, since nothing in the registry marks a point essential) |
| Both installed on one machine | Each keeps its own key and doesn't read the other's secrets | Same | — |

- **`model_group` in the decision point registry**: the platform writes a tier; Muninn writes a task group name or default. In the shared format this field is a free string, so it stays unchanged this time. The reason: Muninn's groups are keys of `llm.models`, and in code each group maps to exactly one tier. If a tool later needs to read the tier from the registry, add a `tier` field then.
- **Thinking effort** (not covered by the platform's design): by default Haiku 5.5 and Sonnet 5.5 decide for themselves how long to think. With API billing, thinking tokens are paid for too; mail sorting alone is about 900 calls a week.
  - v1 lets each provider and each tier set the thinking effort, with fast defaulting to the lowest.
  - Parameter names follow the API docs; look them up during implementation.
  - The Claude CLI has no such setting; it stays as it is.

### 2.3 Implementations still to do for v1

**openai (Chat Completions-compatible)**: covers OpenAI, OpenRouter, Gemini's compatible endpoint, LM Studio and vLLM.
- Photos are sent as data URIs; PDFs are not sent by default, only when declared in the config.
- A strict schema needs every field to be required and `additionalProperties: false`; contracts/llm/*.schema.json doesn't meet that, and sending it as is gets a 400. So convert first, and use non-strict when conversion isn't possible.
- When a gateway rejects `response_format`, put the requirement in the prompt instead, log one warning per model, and list it in check. This does not count as switching models or services.

**ollama (native API)**
- The native API, because the compatible endpoint can't set `num_ctx`, and an over-long prompt gets silently cut from the front.
- Add the images field to /api/chat, sent only for models declared able to read images.

**Common rules**
- **Capabilities**: each model has Capabilities (images, pdf). An unknown model is treated as text-only until it is declared in `llm.capabilities`.
- **check()**: generalizes today's version(): it validates the key and confirms that every configured model is in that provider's model list; it costs no tokens.
- **Choosing models**: the settings page picks from the provider's model list and doesn't guess names (the platform's `agenthub llm setup` does the same).
- **Retries**: 429 and 529 back off according to Retry-After, with a capped number of attempts; if it still fails, it counts as an ordinary failure.

**Deferred**: Gemini's native API, a price table.

### 2.4 Explicit degradation

**Read-file filtering**
- Keep the current approach (the read-file filter in llm_router.py; test_uploads.py's "answered by the one that can read it" must still pass).
- Only when filtering leaves nothing, return `Completion.refused` with a reason:
  - capability: "The model you set up, X, can't read photos, so I've saved this as a note for now. To switch to a model that can read images, go to llm.capabilities."
  - routing: "The privacy setting only lets the local model see photos, and the local model can't read images."
- Text attachments are expanded into text first and are not affected by this rule.

**"No model" mode** (neither cloud nor local is set)
- Rule reminders, plans, tasks and push notifications all work as usual.
- route returns refused="no_model" and tells the user "No model is connected yet; I've saved this as a note."

**Inconsistent settings**: refused only in commands that actually need a model.
- Two kinds of inconsistency:
  - routing: privacy, with a cloud but no local model;
  - routing: cloud, but no cloud. Today this silently falls back to privacy.
- When hit, these commands explain why and exit with code 2: tick, morning, evening, weekly, prepare, reflect, reflection, mail, schedule, eval. serve starts as usual (settings are changed on the web page; stopping it would leave nowhere to change them) and writes the problem to the log and the settings page; `muninn check` lists the problems and exits with 1.
- build() does not refuse; other commands run as usual, and check lists the problems.

**Reflection**: under privacy it goes only to the local model; with no local model, the settings page says why reflection is off.

### 2.5 Users with one key

- Use routing: cloud; with only one provider, `use` can be omitted: all three tiers go to it, and the tiers separate the cheap model from the strong one.
- openai has no default models; the settings page picks them from that provider's list.
- privacy can be chosen only when a local backend exists.
- `--prompt-check` goes through the router instead (cases are not recorded); under privacy only cloud_ok cases go to the cloud.

### 2.6 Evaluation

- `eval --backend <provider:model>`: any configured backend can be a candidate.
- With only one provider, use "the large model's reply + the user's correction" as the reference to evaluate the same provider's small model. When the candidate is the very model that answered originally, use only cases that carry the user's answer.
- The judge comes from the same provider too, so there is self-grading bias; the report has to say so.
- A small invented test set: a few dozen cases each for route, work_check and facts, plus a check that the output of all 22 decision points matches its schema. Run it with `muninn eval --suite`.

### 2.7 The prompts were tuned on Claude

- The points that must hold up: route, work_check, facts.
- No per-provider forks of the prompts. If one provider fails, change the shared prompt, run --prompt-check on Claude to confirm it didn't get worse, and rerun the test set on that provider.
- The settings page shows how many cases each point passes, but never silently turns off a feature because of it.

## 3. What a key-only user gets

| Key only (routing: cloud) | Needs extra setup | Off without an agent runtime |
|---|---|---|
| Conversation, creating domains, plan confirmation, tasks, ideas, topics, undo, rule reminders, morning and evening briefs and the weekly report (need scheduling), push and pairing (need HTTPS), photos and PDFs (the model must be able to read them), application packets, git activity | Google client (in Testing mode, tokens expire after 7 days), Azure app (only work or school accounts), a location app (OwnTracks, or your own LocationApp), iOS Shortcuts, ActivityWatch plus the macOS agent, a local model (to use privacy), project folders, Claude Code activity | Deep talks, hands-on work in sandboxes, permission cards, ask_project answers, promoting a sandbox to a GitHub repo |

- **Deep talks**: SessionRunner plus the v2 contract is already independent of the runtime, so anyone can write their own runner; per the 10/24 decision, a built-in Muninn runner is not being built for now.
- **Must fix in v1**:
  - talk_to_project and ask_project are offered only when the matching capability exists.
  - The settings page lists "features that are off, and why". ✅ (10-31: deep talks, calendar, mail, the weekly reflection, filling in application forms, activity from project folders and from sessions; `muninn check` prints the same list)
  - The "is it live" check has to be possible to turn off on installs without deploy.sh, and judge by version number instead: a wheel install looks at the package version.

## 4. Packaging and install

**Install methods** (decided)
- **The main path is a one-line install**: `curl -fsSL <public-url>/install.sh | sh`. In order:
  1. install uv;
  2. install the wheel, with the JS already built, using `uv tool install muninn-ai` (the package is published as muninn-ai: PyPI's "muninn" is another project; the module and the command stay muninn; contracts are read through importlib.resources instead);
  3. register Muninn as a background service (launchd on macOS, systemd --user on Linux);
  4. open the browser on the settings page.
- **Setup happens on the web page**: pick a provider, paste the key, choose models from that provider's list, and the settings page verifies them on the spot; the same page then shows the QR code for pairing the phone.
- **Docker compose is the secondary path**: for NAS boxes and servers, with data under /data.

**When Docker touches host resources**
- Reach the host's Ollama through host.docker.internal (Linux needs host-gateway).
- To count git activity, mount the workspace read-only into the container and set git_authors explicitly in the config.
- The Claude CLI is not supported in the container. smoke.py reads MUNINN_SECRETS_DIR instead.
- .env is read in by compose's env_file; the docs state that the program itself doesn't read .env.

**First run**
- Defaults for a new install:
  - no model;
  - projects uses local, with an empty folder list;
  - transcript_roots and workspace_roots are both empty;
  - mail accounts are empty and are added on the settings page.
- `muninn check` runs smoke.
- `muninn config --effective` prints the merged config, read-only; secrets print only as paths.

**Secrets**
- Read only from environment variables, or from files with mode 600 in secrets_dir. When one is missing, say what is missing; never quietly switch to something else.
- A key pasted on the settings page is written straight to a secrets file, not through the config file.
- When installed on the same machine as the platform, each keeps its own copy and doesn't read the platform's secrets.

**Scheduling**
- An optional `schedule:` config: serve runs tick, the morning and evening briefs, the weekly report, and the nightly reflect --budget in the same process, sharing create_app's RLock with the web app.
- Without it, external scheduling works as before, and the owner's launchd is unaffected. systemd, cron and launchd examples are included.

**Access**
- HTTPS is a precondition for pairing. The docs cover three approaches, in order: Tailscale serve, Caddy with your own domain, Cloudflare Tunnel. The settings page recommends Tailscale and, if it isn't installed, walks through installing it step by step.
- check warns when an allowlist is set but host is not 127.0.0.1, because then the header can be forged. v1 does not add a replaceable identity interface.

**Backup**
- `muninn backup <dir>` uses the SQLite backup API and backs up domains, config and attribution along with the DB; secrets are backed up only with an extra flag. CI tests restoring.

**Migrations**
- Additive only: new tables, new columns, data fixes that can be rerun.
- A `meta` table records schema_version and min_readable. min_readable is raised only for an irreversible migration; the program refuses only to open a DB whose min_readable is newer than itself. Today's code only does CREATE IF NOT EXISTS and adds columns, so as long as migrations are additive, rolling back to an old version that has no version number is safe too.
- Migrations run under BEGIN IMMEDIATE, in the first process to open the DB; other processes wait on busy_timeout. The four data migrations move into this mechanism too; DeviceRegistry stays additive-only.
- Back up to data/backups/ before migrating. deploy.sh's automatic rollback rolls back only the code; when there has been an irreversible migration, the rollback record gets an extra line, "restore data/backups/…".

## 5. Repo hygiene before going public

1. **Step 0: LICENSE**. The same decision as agent-hub: vendor/records is a copy of the platform contracts, and you are the author.
2. **The public tree is produced with `git archive main`**, never by copying the working directory. First add `.agent-memory/` and all of `.claude/` to .gitignore.
3. **Scanning**: history starts from a single commit, so the thing to guard is that final tree; run `gitleaks dir` on the archive output. Any key it finds is revoked or replaced.
4. **Clear every spot listed in §1.4**. This plan records only locations, not personal values; whether it stays when going public is decided together with the upstream decision in item 3 of §7.
   - The talk.ts prefix is generated from owner_label.
   - When changing prompt examples, run prompt_snapshot plus --prompt-check before and after, to confirm the decisions don't change.
5. **Split the repo**:
   - The public repo is the only upstream for code.
   - The private deploy repo holds only: config files (config, attribution, the laptop agent's config), the launchd plist, deploy records, CLAUDE.local.md, and the private name blocklist.
   - The runtime is repointed once: `remote set-url origin <public-repo>`, `fetch`, `reset --hard origin/main`; data/ is in .gitignore and is unaffected. The development checkout does the same.
   - The old private repo is renamed and archived, keeping its full history; before switching, wrap up every branch and worktree.
   - ✅ Done (1c4148e): Conventional Commits, release notes generated by cliff.toml, deploy.sh doesn't commit, deploy records written to the runtime's data/deploys.log.
6. **The pre-push hook** goes in .git/hooks of the clone that pushes to the public remote; the blocklist itself lives in the private repo.
7. **Docs**: README (one in Chinese, one in English), CONTRIBUTING, SECURITY. Rewrite the README's deploy section to match the current deploy.sh.
8. **CI**:
   - uv sync, npm build, pytest (all with fakes);
   - webkit e2e;
   - install the wheel into a clean venv and run init, check, serve plus /healthz in "no model" mode;
   - docker build;
   - gitleaks;
   - live tests only on manual trigger.

## 6. Phased plan

Every step goes through deploy.sh's tests and smoke, and after each one the owner keeps using Muninn as usual.

0. **License and ignore list** ✅ (10-29)
   - What:
     - add the LICENSE; confirm the license of vendor/records;
     - add `.agent-memory/` and `.claude/` to .gitignore;
     - the owner makes the §7 decisions.
   - Verify: `git check-ignore .agent-memory .claude/anything` matches both.
1. **Pin the owner's values, then change the defaults** ✅ (10-29: with the owner's settings written out, the effective config is byte-for-byte identical before and after the defaults changed)
   - What:
     - First add `config --effective` and print one on the real deployment.
     - Write these values into the owner's data/config.yaml: owner_label, principal_id, timezone, workspace_roots, transcript_roots, projects (external plus three paths), mail_accounts, calendar_name, reflect, every llm key, host and port.
     - Write out the laptop agent's config; make install.sh's Chrome profile name a parameter.
     - Only then change the defaults in config.py, install.sh and muninn_agent.py, and update the assertions about defaults in test_local_projects.py:89 and test_task_models.py.
   - Verify: the real deployment's effective config shows no difference before and after; the laptop still recognizes the work folder; the to-dos marked for the owner in STATE.md are still recognized.
2. **Clean tracked files** ✅ (10-29: every spot listed in §1.4 is cleared; the private name list lives under the runtime's data/, outside the repo, and moves into the private repo in step 8; scanning the whole tree with it finds no hits except the vendored NOTICE)
   - What: UI, tests, docs, prompt examples; example files switched to invented values; mentions of agent-hub in code and docs reworded so they make sense without it.
   - Verify: all tests plus e2e pass; prompt-check shows nothing changed; the blocklist finds no names.
3. **Model interface** ✅ mostly (10-29 to 10-30): "no model" mode, an optional local model, handling of inconsistent settings, `muninn check`, deep-talk tools offered only when there is a runner (cf189f2); Capabilities, check(), expanding text files, refusals with a reason (3e543e0); `llm.points`, thinking effort, token accounting, redaction before sending to the cloud (f74f20e); replies checked against the schema, with one re-ask when they don't fit (this step).
   - Since done: the `find("{")` spots are one reader, core/reply_json.py, which the shape check uses too (10-31: in a day and a half live, the only misfits were a schema that left out an answer the code takes, now fixed and guarded by a test); checking length with `context` before sending, and `location: local` providers (with the ollama implementation in step 5).
   - What:
     - make Implementation a complete Protocol: from_spec, backend, check;
     - Capabilities, refused with a reason, "no model" mode;
     - refusing on inconsistent settings, making the local backend optional (`location: local`), `llm.points`;
     - offering the two tools by capability, expanding text attachments first;
     - the structured-output approach, redaction before sending, thinking effort, token accounting.
   - Verify: test_uploads.py:41-47 still passes; every item above has new tests; the owner's effective config shows no difference; when the owner sends a photo, Claude still reads it.
4. **Evaluation and migrations** ✅ (10-30; a copy of the owner's DB was upgraded, then opened with the previous version of the code, and every task and case could be read)
   - What: prompt-check goes through the router and filters on cloud_ok; eval --backend; the meta table, min_readable, locking, backup before migrating.
   - Verify: a test with two processes opening the DB at once; a test of old code reading a new DB; upgrade a copy of the owner's DB, then open it with the previous version of the code.
5. **New adapters** ✅ (10-30): openai (Chat Completions-compatible), native ollama (image reading, num_ctx), `location: local` (base_url must be on this machine or this network), length check with `context` before sending. The old llm.local_model reads as a local provider named ollama, with the name and label unchanged.
   - What: openai, native ollama (image reading, num_ctx).
   - Verify: MockTransport tests for error classification, the 400 fallback, and check confirming model ids; live tests run by hand; the owner's routing untouched.
6. **Small test set** ✅ (10-30): `muninn eval --suite`, with 21 route cases, 10 facts cases and 14 work_check cases, all invented, shipped with the package; plus a check that replies fit their schema. Baseline: Claude Sonnet (route, facts) plus the local qwen3.6 (work_check) got everything right; Haiku for everything also got everything right. The cases are on the easy side for strong models; they are there to show whether a weaker model can take over. Add cases when a sentence pattern that really trips models up turns up.
7. **Packaging** ✅ (10-30; the one-line install was run against a fake HOME, but the background service part was not registered on this machine; the Docker image was actually built and run, and the container restarts itself after settings are saved)
   - Done: contracts moved into the package (a link left at the root); the wheel can init/check/serve in a clean environment; `muninn backup`; the access warning in `muninn check`; built-in scheduling (config key `jobs`, to stay clear of the `muninn schedule` command); web setup (the one-time link works only while no device is paired; after pairing it is given only to paired devices); install.sh (launchd / systemd --user, MUNINN_SERVICE); Dockerfile + compose (Docker volume, non-root).
   - install.sh installs `muninn-ai` by default (11-01: PyPI's "muninn" is another project), which works once step 9 publishes it; the "is it live" check has no commit to compare against in a package install, so it is off for now (writes info only).
   - What: package data, wheel, one-line install, web setup, Docker, built-in scheduling (the owner doesn't set it), backup, check's warning; in a wheel install the "is it live" check looks at the package version.
   - Verify: CI's wheel and docker smoke (including completing pairing behind TLS); the owner's allowlist still keeps others out.
8. **Where private things live** ✅ (10-30)
   - No separate private repo: none of the private things were in the repo to begin with, so a separate repo would just be one more copy to keep in sync.
     - Config, keys, deploy records, the private name list, the database: in the runtime's data/, backed up every night by the platform with restic to B2.
     - Agent notes (the agent-memory branch): pushed only to another bare repo on this machine; never been on GitHub.
     - launchd and scheduling: the platform's project registry.
     - CLAUDE.local.md: not under version control.
   - Check before pushing main: this checkout's .git/hooks/pre-push scans the commit being pushed with the private name list and refuses to push on a hit (except the vendored NOTICE). All worktrees share it, and deploy.sh's push goes through it too.
   - Verify: a clean Linux (arm64) container, connected only to one OpenAI-compatible endpoint (this machine's Ollama /v1).
     - Went through web setup, pairing and the first conversation, and ran the test set inside the container.
     - qwen2.5:7b on the test set: route 0.67, facts 0.50, work_check 0.79; Claude got everything right.
     - Caught one bug: a 501 was treated as overload and retried; fixed.
     - Also found: Ollama's MLX models don't support /v1 (they return 501), so a regular model is needed.
     - Pairing over HTTPS is covered by e2e (self-signed certificate); this time the container used plain HTTP with the cookie passed directly.
   - What: deploy.sh gets its config from the private repo.
   - Verify: the owner does a full deploy; on a clean Linux VM, with only one OpenAI-compatible key, pairing and the first conversation complete over HTTPS.
9. **Go public**
   - What: git archive plus gitleaks dir; create the new repo and turn on CI; repoint the runtime and the development checkout once; archive the old repo. No more history rewriting after that.

## 7. Decisions for the owner

**Decided**
- **History**: reset; the public repo starts from a single commit; the old repo is renamed and archived, keeping its full history.
- **Changelog**: not committed; Conventional Commits instead; release notes are generated by git-cliff at release time; deploy records stay out of the public repo. Live.
- **Main install path**: a one-line install plus web setup: paste a key, scan the QR code with the phone, and it's ready to use; Docker is left for NAS boxes.

**Decided on the defaults, evening of 10-29**
1. **License** (step 0; agent-hub is waiting on the same decision): the default is Apache-2.0 for both repos and the shared contracts. Example: with AGPL, anyone who modified it and ran it as a service would have to publish the source.
2. **New install defaults**: the default is to start in "no model" mode, with the settings page guiding toward routing: cloud. Example: someone who enters only an OpenRouter key can talk to it once setup is done.
3. **Upstream**: the default is the public repo as the only upstream, with the private repo holding only config and records. Example: fix a bug in the public repo; at deploy time, the config comes from the private repo.
4. **Language** (changed by the owner on 10-30): the docs are in English, and the README is also in Traditional Chinese (`README.zh-TW.md`). Muninn speaks Traditional Chinese or English: the interface, what it says and what it asks the models, chosen by `language` in config, on the setup screen and on the settings page. ✅ (10-30) Live: docs in English; the screens, what the server says and the prompts follow `language`; the eval suite runs each task in both languages (Sonnet 1.00 on route and facts in both; the local work check 1.00 in Chinese, 0.93 in English).
5. **Deep talks**: the default is not built into v1. Example: saying "talk with garden" gets the reply "deep talk isn't turned on".
6. **Project source**: the default is local, with an empty folder list.
7. **Providers**: the default is four: claude, anthropic, openai and ollama, named the same as on the platform; native Gemini is deferred, and Gemini users go through its OpenAI-compatible endpoint.
8. **Whether unknown models can read files**: the default is text only; they can read files once declared. Example: add one line in llm.capabilities and it can read photos.
9. **Whether prompts fork per provider**: the default is no fork. Example: if a small model keeps getting route wrong, change the shared prompt first, verify it on both providers, then ship it.
10. **The macOS laptop agent**: the default is to keep it, marked macOS-only and optional. The work folder and Chrome profile become parameters; the token is pasted in by hand instead of read from the server over ssh. ✅ (10-30) Computer activity is now an interface: `contracts/activity.v1.md`; `/api/ingest` checks against the contract (records that don't fit are rejected one by one with an explanation, and the rest are accepted); Muninn serves the collectors for download itself; the settings page gives a one-line install command and a passcode; the Mac collector is the first implementation.
11. **Thinking effort and budget** (new): the default is the lowest thinking effort for the fast tier, the provider's default for standard, and no budget. Example: when the API bill for a month runs over expectations, set `budget.daily_usd`; once it is exceeded, non-essential decisions stop and a push goes out once.
