<!-- contract: local-projects v1 -->
# Contract: local project folders v1

With `projects.side: local`, Muninn treats the folders listed in `projects.folders` (plus the folders opened for
ideas under `projects.sandbox_root`) as projects; a project's name is its folder's name. Only these folders are
read, written, or lent to a deep conversation.

## What Muninn reads

- `TODO.md`: one item per line, `- [ ] <text>` (`- [x]` is done and does not count). A `## ` heading decides who
  owns the items under it: a heading with `agent` → an agent can do them; with `待定` or `pending` → a decision
  comes first; any other heading, or no heading → only the user can do them.
- `STATE.md`: the project's current state, read whole.
- `.muninn/outbox.yaml`: what the project has to tell Muninn. A YAML list; each item is one message per
  contracts/messages.v1.schema.json, without `project` (the folder name is the project). An `id` only has to be
  unique within this project (different projects can all use `q1`: Muninn records it internally as
  `<project>:<id>` and strips the prefix when it writes back to the inbox); the `task` of a `done`/`drop` is this
  project's own task id.
  Muninn reads it once per tick and takes each `id` only once;
  old items can stay or be deleted. An item in the wrong format is skipped (the rest are still taken).

```yaml
- id: garden-q1
  type: question
  text: Red roses or white?
- id: garden-t1
  type: task
  text: Order soil for the rose bed
  due: 2026-10-30
- id: done-garden-t1
  type: done
  task: garden-t1
  text: Soil ordered
```

## What Muninn writes

- `.muninn/inbox.md`: what Muninn passes on to this project (one entry per contracts/handoffs.v1.schema.json),
  one section per entry, appended only. Each section's first line is `## <time> · <effect> · #<id>`, followed by
  the fields that have a value: about (item), what to do (fact), the user's own words (quote), why (why), the
  message being replied to (reply_to), the task already handled (task). The replied-to message and the task use
  the ids this project wrote itself. The same id is never written twice.
- A folder opened for an idea: `README.md` (what the folder is for) and an empty `.muninn/`. Asking again with the
  same name and the same purpose (a resend) gets the same folder; if the name is already taken by something else,
  the folder is not opened (no suffix is added).

The project's own agent has to know to read `.muninn/inbox.md` and to write what it wants to say into
`.muninn/outbox.yaml`: this goes in that project's own instructions, added by the user; Muninn does not change
other projects' files (except these two).
