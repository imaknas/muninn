# Privacy model

[繁體中文](zh-TW/privacy.md)

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
