# Working on Muninn

## Commit messages: Conventional Commits

Every commit message starts with a type, an optional scope, and a summary:

    <type>(<scope>): <what changes, in the imperative or as what is now true>

- Types: `feat` (something the user sees or Muninn does that it did not before), `fix` (it did something wrong and now does not), `perf`, `refactor` (no behaviour change), `test`, `docs`, `build`, `ci`, `chore`.
- Scope is the area when one fits: `talk`, `tasks`, `mail`, `location`, `llm`, `web`, `records`, `deploy` …
- A change that breaks a contract, a config key, or data an older version can read adds `!` after the type and a `BREAKING CHANGE:` paragraph saying what breaks and what to do.
- The body says why and what the user will notice, as before.

There is no hand-written changelog. Release notes are generated from these messages when a version is tagged (`git cliff --tag vX.Y.Z`, configured in cliff.toml): `feat` raises the minor version, `fix` and `perf` the patch, `!` the major. So a `feat` or `fix` summary is written for the person reading the release notes, not for the code.

Deploying (bin/deploy.sh) commits nothing; it records which version went live in the runtime's data/deploys.log.
