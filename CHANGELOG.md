# Changelog

What changed in each version, generated from the commit messages (git cliff) when it is tagged; only new features,
fixes, speed-ups and reverts are listed.

## v0.8.0 (2026-11-02)

### New

- **llm:** The Codex CLI can answer Muninn's requests, beside the Claude CLI, with every tool switched off
- **setup:** `muninn uninstall` takes Muninn off the computer, and reinstalling or upgrading restarts what runs

### Fixed

- **setup:** A new user can get in from the terminal and from Docker, and setup failures say what went wrong
- **setup:** Tailscale without Serve, unusable models, local servers and a stuck install are said or handled
- **deploy:** Muninn uninstall mentions systemd's linger only where systemd ran Muninn

## v0.7.0 (2026-11-01)

### New

- **activity:** Codex sessions count as your time and go into the weekly reflection, beside Claude Code's

### Fixed

- **records:** A record id sent twice with other content shows the same, whichever arrived first

## v0.6.1 (2026-11-01)

### Fixed

- A malformed PDF among your documents can no longer hang Muninn or exhaust its memory

## v0.6.0 (2026-11-01)

### New

- **setup:** The settings page sets up the folders Muninn reads, Google mail and calendar, and form filling

## v0.5.0 (2026-11-01)

### New

- Muninn has a logo, a raven's head, on its app icon and at the top of the README

### Fixed

- **records:** A to-do that first came as a v1 message and is raised later reads as the words raised
- **tasks:** A project changing one item of a merged to-do no longer renames the whole to-do
- **install:** The one-line install works on Linux without systemd or time zone data, and says when Muninn did not start

## v0.4.0 (2026-11-01)

### New

- **records:** Muninn speaks Principal Handoff 0.5, and still 0.4 to a project configured for it

### Fixed

- `muninn upgrade` installs a release published minutes ago, and says why when it cannot
- **web:** Leaving the page no longer starts reads the browser refuses and reports as errors

## v0.3.0 (2026-11-01)

### New

- **setup:** Finish setting up in this computer's browser, and pair the phone later from the settings page
- **setup:** The settings page gives Muninn its HTTPS address with Tailscale, so the phone can pair

### Fixed

- **setup:** A model that cannot chat is not offered or saved, and having no model at all is said
- **talk:** A deep talk's past conversation that fails to load no longer raises an error on the page

## v0.2.0 (2026-11-01)

### New

- `muninn upgrade` moves an installed Muninn to the latest stable release

## v0.1.0 (2026-11-01)

The first release; development before it happened in a private repository. Muninn is an always-on personal
assistant you talk to in one conversation, on your phone or in a browser:

- keeps track of what you are working towards (goals, deadlines, to-dos from your projects) and brings up what waits
  on you, once, at a sensible moment;
- reads what you let it (mail, calendar, what you do on your computers, where you are) and acts where it can, each
  step undoable: calendar blocks, status changes, application packets and forms, reply drafts;
- works with Anthropic, any OpenAI-compatible service or a local Ollama model, and keeps what must stay on your
  machine on it;
- talks with your projects' agents over Principal Handoff 0.4, where only you settle what is yours.

