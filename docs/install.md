# Installing and running Muninn

[繁體中文](zh-TW/install.md)

On macOS or Linux, one line installs Muninn, starts it in the background (it
runs its own schedule), and opens its setup page: pick a model, paste its key,
and start using it in that browser. The phone pairs over HTTPS, by scanning a
code on the settings page once Muninn has an HTTPS address (see below).

```bash
curl -fsSL https://raw.githubusercontent.com/imaknas/muninn/main/install.sh | sh
```

Or, with [uv](https://docs.astral.sh/uv/): `uv tool install muninn-ai`, then
`muninn init` and `muninn serve`, and open the setup link it prints (the
package is muninn-ai; the command is `muninn`).

To move to a newer release, run `muninn upgrade`: it installs the latest
stable one, restarts the background service on it and checks that it answers,
and puts the version before back if it does not (`muninn upgrade --check` only
says what is installed and what is out).

Your phone reaches Muninn over HTTPS: Tailscale is the shortest way there. With
Tailscale signed in on the computer (and HTTPS certificates on for the tailnet),
the settings page's "Pair a phone" sets it up itself (`tailscale serve`, for your
tailnet only) and restarts Muninn on the address; otherwise it says what is
missing. The phone needs the Tailscale app too, signed in to the same account.
Muninn runs while the computer is on and awake: on a laptop it pauses while the
lid is closed.

### Docker (a NAS or a server)

Clone this repository there and run `docker compose up -d` (see `compose.yaml`);
the setup link is in `docker compose logs muninn`. The port is published on that
computer's own address only: from another computer, open it through SSH
(`ssh -L 18795:127.0.0.1:18795 <server>`, then the link). To move to a newer
release: `git pull && docker compose up -d --build`. For the phone, run Tailscale
on the server itself (`tailscale serve --bg --https=8444 http://127.0.0.1:18795`),
put the address it gives in `.env` as `MUNINN_PUBLIC_URL=https://<name>:8444`,
and run `docker compose up -d` again; "Pair a phone" then shows the code. Ollama
on the server is `http://host.docker.internal:11434` on the setup page. `muninn backup
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
