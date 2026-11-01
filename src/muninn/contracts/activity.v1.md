# What the user does on a computer (v1)

A **collector** on a computer sends Muninn what the user does there: which window was in front and for how long, and
the commits they made. Muninn reads only what is written here (`src/muninn/core/activity.py`), whoever sent it; a
collector for another system is another sender of the same thing.

## Sending

`POST /api/ingest`, the ingest token as a Bearer token, a JSON body `{"observations": [...]}`. The answer is
`{"ok": true, "new": <how many were new>, "refused": [{"index": <position in the list>, "why": "..."}]}`: what keeps
to this contract is kept even when others in the same post are not, so a collector moves on rather than sending the
same post again. An observation already kept (same `source` and `key`) is not kept twice; sending again is safe.

Every observation:

| Field | |
|---|---|
| `source` | the collector's name: lowercase letters, digits, `-`, `_` (e.g. `aw`); `git` for commits |
| `key` | what makes it one observation for that source (at most 200 characters) |
| `ts` | when it began, ISO 8601 with a time zone |
| `kind` | `window`, `commit` or `agent_commit` |
| `payload` | what it was (below); fields not listed are dropped |
| `sensitivity` | `public`, `personal` or `sensitive`; left out, `sensitive`. Muninn's attribution can only raise it |

## `window`: a window in front

| `payload` field | |
|---|---|
| `app` | the application's name (empty when the collector could not tell) |
| `title` | the window's title (may be empty) |
| `duration_s` | seconds it was in front while the user was at the computer, 0–86400 |
| `browser_profile` | optional, for a browser window: `work` or `personal` |
| `device` | optional: which computer |

Time away from the computer is not sent as windows. `key` is the collector's own: the same window sent twice has the
same key.

## `commit` and `agent_commit`: a commit made

`agent_commit` is one an AI agent co-wrote (it counts as the user's output, not as hands-on time). Sent with
`source: git` and `key: <repo>:<commit sha>`, which is how Muninn's own git source keys a commit, so one seen both on
a laptop and on the server counts once.

| `payload` field | |
|---|---|
| `repo` | the repository's name (its folder under a workspace root) |
| `files`, `added`, `deleted` | how many files, lines added, lines deleted |
| `exts` | file extension → `{files, added, deleted}` |
| `langs` | the languages those extensions are |
| `machine` | optional: which computer |

No commit message, path or diff is sent.

## A collector

Each collector is a folder under `src/muninn/collectors/` and one entry in `COLLECTORS` there: its installer first,
then its other files. Muninn serves them at `/collectors/<name>/<file>` with its own address written into the
installer, and the settings page (電腦活動) shows the one line that installs it (`curl -fsSL … | bash`) and the
ingest token the installer asks for. The Mac collector (`mac`) reads ActivityWatch and the Mac's repositories.
