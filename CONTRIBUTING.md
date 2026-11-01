# Contributing to Muninn

Muninn is one person's assistant that anyone can run for themselves. Changes are welcome when they keep it that way:
self-hosted, private by default, and quiet unless something matters. Open an issue first for anything bigger than a
fix, so we can agree on the shape before you write it.

## Setting up

```bash
uv sync --extra autofill                     # Python 3.13; the extra fills in application forms
npm ci && npm run build                      # the PWA's TypeScript → web/static/*.js (strict, type-checked)
uv run playwright install webkit chromium    # the browser tests run the PWA in WebKit; Chromium fills forms
```

No model, account or key is needed to develop or test: every test uses fake models, a fake clock and invented data.

## Tests

```bash
uv run pytest -n 4         # unit and API tests, 4 at a time: fast, run them all the time (one file or -k: leave -n off)
uv run pytest -m e2e -n 8  # the PWA in a real browser, as a phone and as a laptop, over HTTPS (8 at a time)
uv run pytest -m deploy    # bin/deploy.sh against a scratch remote and runtime
```

Every change comes with tests, and all three suites pass before it is merged. A few tests guard things that are easy to
change by accident; when one fails, it says what to run:

- `tests/golden/command_prompt.txt` is the routing prompt word for word. A change to a tool's description or to the
  prompt shows up there; look at the diff, then write the new one:
  `uv run python bin/prompt_snapshot.py --language zh-TW | jq -j .route > tests/golden/command_prompt.txt`.
- `src/muninn/contracts/web-api.v1.schema.json` is generated from `ts/types.ts`: change the types, then run
  `uv run python bin/web_api_schema.py`.
- `src/muninn/contracts/llm-points.yaml` declares every place a model is asked something, with the schema of its reply; a new
  `LLMRequest.task` needs an entry there (docs/design/llm-points.md).

Tests use invented people, companies and data only, never anything real.

## How the code is laid out

`src/muninn/core/` is the logic and depends only on the ports in `core/ports.py`; `src/muninn/adapters/` holds the
implementations (SQLite, YAML plans, mail and calendar providers, model providers, the web app, application form
sites); `src/muninn/main.py` is the one place they are chosen and wired together (README.md, "Layout").

- **Dependencies come in from outside.** Core code takes what it uses (a store port, a clock, a model router, a
  callable) as constructor or function arguments; it does not create clients, read files or settings, or look at the
  time itself.
- **A known variation point is an interface.** Where there are, or will certainly be, two or more ways of doing
  something (model providers, mail providers, the sites application forms are filled on, notification channels), write
  one Protocol and one implementation per way, chosen by the caller. Don't branch on the kind with if/elif.
- **No interface for its own sake.** Where there is one way of doing something and no second in sight, write it
  plainly.
- **Actions go through the gate.** Anything Muninn does on the user's behalf is an Action in `core/actions.py`: the
  gate decides whether it is done with an undo or asked first, and anything that cannot be taken back once out is
  always asked first.

## Words and languages

Muninn speaks Traditional Chinese (Taiwan) and English, chosen by the `language` setting.

- What the server says is in `src/muninn/languages/<language>/<area>.py`; what the screens say is in
  `src/muninn/adapters/web/ts/words/<language>/<screen>.ts`. A new text goes in both languages. The English screen
  catalogs are typed against the Chinese ones, so a missing text does not compile.
- What Muninn asks a model is written in English. Where the answer is something a person reads, the prompt says
  "in {language}", and `written_in()` fills in the language Muninn speaks.
- Fix a model's misjudgment by fixing what it is given (a missing option, field or definition) or by a check in code,
  not by adding counterexamples to the prompt. `muninn eval --suite` shows how a model does on the shipped cases, per
  language.

## Commits

Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/): `<type>(<scope>): <summary>`.

- Types: `feat`, `fix`, `perf`, `refactor` (no change in behaviour), `test`, `docs`, `build`, `ci`, `chore`.
- The scope is the area, when one fits: `talk`, `tasks`, `mail`, `packets`, `llm`, `web`, `records`, `deploy` …
- A change that breaks a contract, a config key, or data an older version can read adds `!` after the type and a
  `BREAKING CHANGE:` paragraph saying what breaks and what to do.
- The body says why, and what the user will notice.

Nothing in the changelog is written by hand: `CHANGELOG.md` and each release's notes are generated from these
messages (`git cliff`, configured in `cliff.toml`), so a `feat` or `fix` summary is written for the person reading the
release notes.

## Releasing

`feat` raises the minor version, `fix` and `perf` the patch, `!` the major (`git cliff --bumped-version` says which).

1. Set `version` in `pyproject.toml`, then `git cliff --tag vX.Y.Z -o CHANGELOG.md`, and commit both as
   `chore(release): vX.Y.Z`.
2. `git tag -a vX.Y.Z -m vX.Y.Z` on that commit, and push the commit and the tag.

Pushing the tag does the rest (`.github/workflows/release.yml`): the package is built from that tag with its web app,
the GitHub release is made with the notes `git cliff` generates, and the same files are published to PyPI as
`muninn-ai`, which is where `muninn upgrade` looks for the latest stable release (a pre-release or a yanked version is
never taken). PyPI trusts the workflow itself (Trusted Publishing, environment `pypi`), so no token is kept anywhere. A
tag already pushed is released from the Actions tab: Release → Run workflow, using the workflow from that tag (the
`pypi` environment accepts only `v*` tags).

## Privacy

Read README.md, "Privacy model", before touching anything that sends data out of the machine: the model router, the
cloud payloads in `core/privacy.py`, push notifications, mail, application forms. A change that sends something new
out says so in its description, and comes with a test that shows what does and does not leave.

## License

By contributing you agree that your contribution is licensed under the Apache License 2.0 (LICENSE).
