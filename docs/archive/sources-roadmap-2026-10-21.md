# Observation sources: plan and feasibility

> **Archived 2026-10-24.** The plan as it stood on 2026-10-21; parts no longer describe Muninn. Since then mail is read from the whole personal inbox (and work mail through Microsoft) with reply drafts, the calendar gets Muninn's own blocks, and with routing "cloud" the cloud model reads mail and what you type. What Muninn does now: README.md and the git log.

Everything ships into the same ingest API (`POST /api/ingest`, bearer token)
or is read locally on the server. Message content is always `sensitive`: it is
only ever processed by the local model.

| Source | When | How | Notes |
|---|---|---|---|
| Plans and trackers | v1 | read `data/domains/*` | changes count as domain activity |
| git activity | v1 | `git log` metadata | AI-co-authored commits recorded separately |
| Claude Code sessions | v1 | transcript metadata | attended activity only |
| Window activity | v1.5 | ActivityWatch on the laptop + a small shipper → ingest | `kind: window`, payload `{app, title}`; `windows:` rules in attribution.yaml |
| Mail (job label only) | v1.5 | IMAP `SELECT` one label, or Gmail API with that label | ATS confirmations set status to `applied` with undo; interview/rejection mails go to local classification, then the confirmation queue |
| Steps, sleep | v1 | iOS Shortcuts automations → `POST /api/ingest/health` (bearer ingest key), `{"steps": n}` / `{"sleep": total}`; the PWA's 'me' page shows the steps | `steps` counts for the `steps` metric (largest of the day); `sleep` (last night, in seconds, minutes or hours) beats the activity-gap estimate for `sleep_hours` |
| Location | v1 | OwnTracks (HTTP mode) → `/api/ingest/owntracks` (Basic auth, ingest key as password; the PWA's 'me' page configures the app) | sensitive; stays and named places (with an optional domain) on the home page and in the evening summary |
| Time away from the keyboard | v1 | an iPhone Focus (e.g. 閱讀) on/off through Shortcuts → `POST /api/ingest/focus` `{"focus", "state": "on"|"off"}`; long gaps between activity in waking hours | one question per stretch (focus off, or at most two gaps a day, none the calendar explains): which hours goal it counts toward, or none; kept as an `offline` observation the hours metric counts |
| iMessage | v2 | read `~/Library/Messages/chat.db` on the laptop (Full Disk Access; complete only with Messages in iCloud on) | read-only SQLite copy; content sensitive; ship metadata + local-model summaries, not raw text, unless the shipper and Muninn are on the same machine |
| LINE (personal) | not feasible cleanly | no personal-account API; desktop database is encrypted | the only partial route is notification previews from macOS Notification Center's database (Full Disk Access, truncated text, misses muted chats); treat as "not supported" unless that trade-off is accepted |
| Calendar (personal) | v2 | read-only Google Calendar | |
| Voice memos | later | mlx-whisper locally | |
| Company content | blocked | needs admin approval and a policy decision | |
