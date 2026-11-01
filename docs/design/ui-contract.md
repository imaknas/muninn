# The UI contract: what to keep through a redesign

Status: in effect (2026-10-24; on 10/25 switched to the conversation-centered screen). The safety net to put in place before a big change to the UI: as long as the two contracts below hold, the screens can be redone any way,
and the tests will tell you what broke.

## 1. Web API (backend ↔ screen)

- **Source**: `src/muninn/adapters/web/ts/types.ts`. The final `Api` interface is "each path → what it returns on success";
  the other interfaces are the various shapes inside the responses.
- **Generated**: `uv run python bin/web_api_schema.py` turns it into `src/muninn/contracts/web-api.v1.schema.json` (JSON Schema).
  Rerun it after changing types.ts; a test checks that the two agree.
- **Rules**: a response may carry extra fields (adding is not breaking); a declared field without `?` must be present, with the right type.
  4xx always returns `ErrorReply` (`{error}`). Removing a field or changing its type = changing this contract, and the screens must change at the same time.
- **Tests**: `tests/test_web_api.py`
  - Every path on the server is in the contract or listed in `MACHINES` (paths called by machines: data intake, project messages, permission requests,
    health checks, streams, files; each of their formats has its own contract). The contract also has no paths that no longer exist.
  - Each path is called once the way the screen calls it, and checked in two worlds: **new user** (nothing exists; nulls and empty lists show up here)
    and **full** (`full()` in `tests/world.py`; every page has data; the shape of every kind of item in a list shows up here).
  - In the full world no list may be empty (otherwise the item shape is not checked); if a type changes or a field goes missing, the test really catches it.

When redesigning, you can change the shapes in types.ts, but think it through first: a change there means the backend has to change too.

## 2. Names on the screen (screen ↔ user flow tests)

`tests/e2e/test_flows.py` is the things a user does (one set for phone and one for wide screen). It finds things only by **the names a person or a screen reader is told**,
not by layout or CSS class. All the ways of finding things are in `tests/e2e/screen.py`. The screen is the conversation-centered version
(docs/design/conversation-first.md): the conversation, the input box, one button at the top, and the panel it opens. A new screen has to provide these names:

**Everywhere**
- The input box "Talk to Muninn" (textbox), only one; the conversation is "Conversation with Muninn". The input box stays while the panel is open, and what you say is about the item that is open.
- Undo is spoken: say "undo" (or "undo" + which one), and "Undone: …" appears in the conversation. There is no undo button on the screen.
- When Muninn judges that a sentence belongs to another conversation, a divider "Switched to: <topic>" appears above it; a topic without a name gets no divider.
- The button at the top: its name is "N waiting on you, M today; open to see all and details" (when nothing is waiting on you, just "M today; …"), and `aria-expanded` says whether the panel is open.

**The panel "Today and details"** (complementary)
- Regions "Waiting on you" and "In progress"; each item is a link whose content includes the title and a one-line status; clicking it opens that item's details (its card, and "Talk about this in the conversation").
- Navigation "See more": four links, Today, Progress, Lists, Settings; each name starts with the place's name followed by a one-line summary (e.g. "Lists 1 task · 1 idea").
- "Collapse" closes the panel; "Back to today's items" goes up one level.
- A topic under discussion is in "Waiting on you" when it has a question waiting on the user (the content is that question), and in "In progress" while a deep talk is running on it; opening it shows a card with the button "Back to this conversation".
- The questions a deep talk left behind are one entry, "X's deep talk left N questions"; opening it lists those questions, with the button "Back to that deep talk".
- The panel lists the server's list (`GET /api/waiting`), in its order; each entry opens its details by its id.
- Lists: regions "Tasks", "Ideas", "Shelved", "Topics wrapped up today"; each entry is a button named by its title; pressing it starts (or returns to) a conversation about it.
- Today: region "Today's timeline": calendar event titles; what Muninn scheduled is written "Scheduled by Muninn: <title>"; what was done is written as the domain's name; window titles never appear.

**In the conversation**
- Things waiting on you are not in the conversation (only in the button at the top and in the panel); the only cards in the conversation are deep talk requests and the new user's "Start here".
- Cards: a plan item is the region "Want to confirm with you", with the button "Discuss"; when two or more items are waiting in the same domain, "Accept all of '<domain>' as drafted (N items)".
  A reminder card has "Seen it"; an application packet has "Sent, mark as applied".
- New user: region "Start here", link "Connect calendar, mail, location", button "Start" (the input box is first filled with "What I care about lately is:").
- Deep session requests (small cards in the conversation): "<project> asks you" (the options are the buttons), "<project> wants to change files" ("Allow this once", "Deny", "Allow changes to this folder for this whole deep talk"),
  "<project> wants to run a command" (only "Allow this once", "Deny"); an idea's exploration project has "Allow hands-on", and while hands-on is on it reads "Hands-on allowed (asks you first each time)".

**URLs**: empty (just the conversation), `#things`, `#item/<id>`, `#today`, `#progress`, `#lists`, `#settings`, `#packet/<id>`, `#mail/<account:id>`,
`#reflection/<id>`. Old URLs still open: `#home` `#inbox` → the conversation, `#me` → settings, `#tasks` `#ideas` → lists, `#tracker` → progress,
`#places` `#reviews` → today. Opening a push notification goes to `#things`.

**On every screen**: no text like null, undefined, NaN or [object Object]; the wide screen does not scroll horizontally at either 1440 or 1200 wide.

**Removed**: the inbox's keyboard shortcuts (j / k, ⌘K) were removed together with the inbox; for keyboard use, the input box is always there.

## 3. Tests that follow the layout

`tests/e2e/test_layout.py` checks what **this design** looks like: light, the conversation in one centered column, the input box at the bottom; on wide screens the panel is on the right
and does not cover the conversation; on phones the panel comes up from the bottom and stops above the input box. When redesigning, replace the whole file with the new design's own layout tests.
