# Conversation-first Muninn (design)

Status: stage a is done (2026-10-25); b is next. This replaces the "inbox plus a conversation bar" interface direction;
the API contract in docs/design/ui-contract.md stays as it is.

## What the user wants

Opening the home screen means talking to Muninn. This is the main source of interaction. The user states an idea, Muninn discusses it with them, runs experiments on its own during the discussion,
and, if it is worth it, actually turns it into a project. It does not reply "noted" and then offer two options for the user to choose between looking into it and shelving it. How sessions are leased behind the scenes
and how things are routed is not the user's concern; it should just feel smooth to them. If the user suddenly talks about something else halfway through, the entry point decides on its own whether to switch over or to borrow the other channel into the conversation.

## What is wrong now

- After an idea is noted, the user gets "actually look into feasibility / shelve it for now". The idea itself already means "I want to figure this out"; the user should not be asked again whether to look into it.
- Going deep needs `/attach`, acting needs a press on "Allow hands-on", ending needs a press on "take back". These are the system's business, not the user's.
- Changing topics means the user has to switch conversations and take back the deep talk themselves; one conversation can only cover one thing.
- The home screen is a pile of cards, and the conversation is an add-on; things waiting on the user and the conversation live in two different places.

## The new shape: one conversation

Principle: the fewer components and the more they can do, the better; do not lay out a pile of information for the user to process. The whole screen has only four things: **the conversation, the input box,
one button at the top right, and the one panel that comes out when it is pressed**.

- **The home screen is only the conversation**, the same on phones and wide screens, light theme only. There is no sidebar on screen, and nothing on the side like "Next today":
  while the user is talking something through, every other component is a distraction. **Things waiting on the user do not go into the conversation** (decided 10/25 after the user saw the new home screen: a pile of cards dropped into the conversation
  for them to read through was too cluttered): they live in the top-right button and the panel. If they are raised in the conversation, it is Muninn speaking up: when the user pauses, one at a time,
  in one sentence that says clearly what they need to decide, and the user answers by saying it (if there is a button, it goes only with that sentence). A divider is drawn only when it has a real name; no
  placeholder text like "one thing" or "other things".
- **The top-right button**: normally it says just one line, "2 waiting on you · 3 today", with a down arrow on the right to show it can be opened. When nothing is waiting on the user
  it says only "3 today". Everything outside the conversation is reached through this one button; the screen has no separate "Details", topic bar, or sidebar.
- **The panel it opens** (on wide screens it slides in from the right and the conversation stays put; on phones it pulls up from the bottom). The same panel has three layers:
  1. **Today's things**: what is waiting on you and what is in progress, one line each with a name and a one-sentence status; shelved and closed are merged into one line, "2 shelved · 4 closed today",
     which expands only when tapped. At the bottom are four entries: Today, Progress, Lists, Settings, each one line of text plus a one-sentence summary ("Progress · 4 goals, 1 behind"),
     with no icons (a row of icons looks like a phone's tab bar). "Collapse" at the top right.
     The panel background is one shade darker than the conversation, and each group is a white card; group names have a color and a number (waiting on you is an orange solid number, in progress is green), so the groups are told apart at a glance.
  2. **One thing**: tapping a thing switches the panel to its details: name, status, what Muninn is waiting on from the user (one sentence), a few known numbers or dates,
     and a few "talked about" points in time (tapping one scrolls the conversation to that part). No buttons: to do something, say it in the input box. Back at the top left.
  3. **The four entries**: the original eight pages merged into four, all in the same panel, back at the top left. Mail has no page of its own: a mail the user needs to handle becomes a thing
     that shows up in the conversation, and the original is read in the mailbox. All four are view-only, with no buttons (linking an account is the exception; that has to be a press); to change something, say it in the input box.
     - **Today**: three numbers (hours worked, how many events are left, where the user is), a top-to-bottom timeline (work done is solid, events are outlined boxes,
       what Muninn scheduled is a dashed box, the red line is now), and hours spent at each place.
     - **Progress**: one card per area; each goal has a progress bar plus a thin line for "where it should be today on schedule"; ones that are behind say in orange how far behind they are.
       Applications go in their area's card, the latest three plus "N more"; deadlines show how many days are left.
     - **Lists**: tasks (deadline, who asked), ideas (status), and shelved items, one group each; closed ones are merged into one line.
     - **Settings**: four groups, model, notifications, accounts, data sources; each row says only what it is now; "to change it, tell Muninn".
- When Muninn mentions a thing in the conversation, that thing's name can be tapped, and it opens that same thing in the same panel.
- **On phones**: the same panel pulls up from the bottom; tapping into a thing or one of the four places pulls the panel up to nearly full screen, with back at the top left. The panel for one thing
  carries the input box at the bottom, so the user can speak while looking at the details, without closing it first.
- **The user treats the conversation as an all-purpose assistant**: whatever they say, Muninn finds on its own where to hand it off, or brings in the right channel (a project's session),
  and carries it on as back-and-forth conversation. The user does not have to pick "what we are talking about now" first: the input box has no topic tag, and Muninn decides the topic.
- **One conversation, with topics inside it**: every message belongs to a topic (an idea, a project, an application package, or general matters). On screen it is
  one stream; when the topic changes, a thin divider appears: "Now talking about: 3D reconstruction of the room", so when the user scrolls back they can see which part was about what.
  This is Muninn's judgment, not something the user sets (if it judges wrong, the user says "No, I'm talking about the dentist" and it is fixed).
- **What a project's session says** is marked in a different style that shows who it is ("idea-7's experiment"), but the user does not attach and does not take it back.
- **Undo is spoken**: there is no "Undo" button on screen. The user says "undo", "scrap that last one", "cancel the dentist reminder", and Muninn decides which thing it is
  (if none is named, it is the most recent one), undoes it, and says what it undid; if it cannot tell which one, it asks one question.
- **What was talked about today is visible at a glance**: that is the top-right button: how many are waiting on the user, how many today; opening it shows today's things. Even with many topics, nothing is forgotten,
  neither what was raised nor what has not settled yet. For what is not closed, Muninn brings it up once when the user pauses (see "When things pile up" below).

## Two scenarios

**Scenario one: an idea, from chat to experiment to project**

> User: I want Opus to rebuild a 3D model of my room from 2D photos, then give space-planning suggestions. Can that be done?
>
> Muninn: Partly. A single photo can't give accurate dimensions; with several angles and one object of known length (an A4 sheet, say),
> it can get to "will the furniture fit" accuracy. Where it gets stuck is the position of windows and doors. Which do you want to know first: whether the reconstruction is accurate,
> or whether the planning suggestions are good?
>
> (Muninn records it as idea 7, and the topic becomes "3D reconstruction of the room", without asking the user separately)
>
> User: First see whether the reconstruction is accurate. I have photos of the living room.
>
> Muninn: Then I'll open an experiment project to try it: three photos and an A4 sheet as the scale, and see how far the reconstructed wall lengths are from what you measured. Upload the photos and it starts.
>
> (The user uploads photos. Muninn opens idea-7's sandbox and borrows its session to do the work; the experiment runs in the sandbox's own folder)
>
> idea-7's experiment: Reconstructed the living room from three photos; walls are 4.1 m / 3.3 m.
>
> Muninn: The experiment is done. It got 4.1 m for the long wall. What did you measure? Within 10 cm is good enough for planning furniture.
>
> User: 4.05. Very accurate.
>
> Muninn: Then this is worth making a real project: photos → 3D → layout suggestions. Want me to upgrade idea-7 to a project?
> After that it will have its own to-dos, and its progress will show on the "Progress" page.
>
> User: OK.
>
> Muninn: Upgraded. It's called shelf-planner.

**Scenario two: halfway through an experiment, talking about something else**

> (idea-7's experiment is still running)
>
> User: Oh, what time is the dentist tomorrow?
>
> Muninn: Tomorrow at 10:30, at the one on Ren'ai Road. Want me to remind you to leave at 10:00?
>
> (This message is for Muninn, not for the experiment; the experiment keeps running and is not interrupted)
>
> User: Yes. Is that reconstruction from before done?
>
> Muninn: (back to "3D reconstruction of the room") It's done, the walls are 4.1 m...

## When the user is still asked

The rule is unchanged: what Muninn is sure of, it does, and says it did; when unsure, it asks one question; anything outward-facing, or that cannot be undone once done, it asks first.
Under this rule:

- Ideas no longer ask "look into it or shelve it". An idea itself means discussing it; shelving happens when the user says "leave it for now", or when it has not been touched for a long time, and then Muninn does it itself and tells the user.
- Opening a sandbox, borrowing a session, taking a session back: none of these are asked, and none are shown as steps.
- Upgrading to a real project is asked (it creates a repo and appears in the project list, a bigger change), but only once, in one sentence.
- Experiments in the sandbox no longer ask the user for every file write and every command; they ask only to touch things outside the folder, or to reach the network outside the allowlist. The sandbox itself is already locked down by the system:
  it can only touch its own folder, cannot read other projects, and the network has an allowlist.

## How it is done

1. **Every message is first judged for who it is for**. A new decision point, `turn_topic` (multiple choice, `kind: decide`): does this message continue the current topic, switch to another
   ongoing topic (listed), or start a new thing? Like other decisions it follows the routing setting (the user's setting is everything to the cloud); messages marked
   local-only, such as deep-talk handoffs, are not included. Messages that name a number ("continue the idea1 discussion") do not ask the model and go straight to that thing's conversation. It is registered in
   `contracts/llm-points.yaml`, and its accuracy is measured with evals.
2. **The topic decides who takes over**: general matters go to Muninn itself (the current understanding and tools); a project's or an idea's experiment goes to that project's session.
   Borrowing is automatic when needed: when the user asks about a project ("How far along is the drainage in the garden project?"), the `talk_to_project` tool hands this part of the conversation to
   that project's session; when an idea needs an experiment, it is handed to its sandbox. Returning happens without asking: when the session says it has gone idle, or the user moves on to something else, it is handed back
   directly in the background (the handoff summary is written back as before), and there is no more push asking "keep going?"; if the session is doing something (an experiment running, an answer half-written), it finishes this round
   before being handed back. When the same conversation talks to the same project again, it reconnects to the original session (which remembers everything from last time), and is told only what was said in the meantime,
   so a wrong topic judgment or a too-early return just means reconnecting; whether to reconnect is Muninn's judgment of the user's message;
   for one still writing its handoff, the screen immediately shows one line, "Reconnecting to the earlier talk with X", the message is held, and once the handoff is written it is sent in the background
   to the same reconnected session, and the answer streams back as usual (the user does not wait); one returned because it went idle is not reconnected, and Muninn answers. When a session is borrowed, the program appends a fixed sentence after the reply: what was opened, and who what the user says next
   will go to, regardless of what the model's reply says. All the user sees is the conversation.
3. **Ideas move forward on their own**: an idea is a topic, not a "noted" card. After each round of discussion, the decision point `idea_next` (keep discussing / experiment /
   upgrade to a project / leave it for now) decides Muninn's next step; when an experiment is needed, it writes the experiment goal, opens the sandbox, hands it to the session, and brings the result back into the conversation.
   Implementation (`core/idea_next.py`): in a round under discussion, the next step is written together with the reply (the routed reply's `next`: discuss / experiment /
   offer_experiment / promote / shelve), so the reply knows what will happen next and there is no extra wait for the model; the program dispatches on the label and does not read the reply's text.
   If this round's routing already handled the idea with a tool (tools declare `acts_on` themselves), nothing is added. Experiment → open the sandbox per `explore_idea` and hand the conversation
   over (the sandbox opens with the discussion so far, including what Muninn just said it would try); needs the user's nod before trying → only a "try it in a sandbox" button is shown; leave it for now →
   shelve it per `settle_idea` and say so (can be undone); upgrade → ask one question after the reply, with one button, and once the conversation records "already asked" it is not asked again.
   When an experiment is handed back (after the handoff is written back to the conversation, with no reply being written), `idea_next` is asked on its own in the background (whether to ask about upgrading); if worth it, the question is asked in that part of the conversation,
   with a push "idea-N's experiment is done". Sentences in the reply that tell the user to type a slash command (`/attach` and the like) are removed by the program. The upgrade itself (`promote_idea`) goes through the external project system's
   `POST /v1/sandboxes/<name>/upgrade` to turn the sandbox into a real project (a private GitHub repo, optionally renamed).
4. **Storing one conversation**: one stream on screen; internally it is still stored in segments, each tagged with a topic. What project sessions say is still stored separately as before; Muninn's understanding
   does not read it, only the handoff summaries (the privacy design is unchanged).

## When things pile up: ten things at once

Each thing is a topic, and topics have these states, which decide where they go on screen and when they are brought up:

| State | Meaning | Example |
|---|---|---|
| Waiting on you | The conversation has an open question for the user (`ask`, a sentence the user understands without reading the conversation) | "Have you measured the backyard wall? How long is it?" |
| In progress | Muninn or a project's session is working on it | In a deep talk with shelf-planner |
| Talking | Talked about, not waiting on anyone (coming back from a deep talk also lands here: "Talked with X until <time>") | Wants a new phone, talked about it once |
| Shelved | The user said to leave it for now, or nobody touched it for a week | |
| Closed | Nothing left to do | Dentist reminder is set |

**"Waiting on you" is a single list** (core/waiting.py): each kind of thing that waits on the user is one source (Muninn's questions, conversation questions, project questions,
leftovers from deep talks, plan confirmations, application packages, tasks close to their deadline, permissions…), listed once where it is assembled, in display order. The panel, Muninn speaking up,
the morning and evening briefs, and the numbers all read this one list. When a thing has its own card, the conversation about it is not listed separately.

**Things that happen elsewhere**: when a deep talk ends (handled only once), the questions that conversation asked before are closed (can be restored), the topic goes back to "Talking", and the topic memory is refolded up to
the end; the questions it left this time (the handoff's open_questions) stay with that conversation and are handed to the session to ask the next time it is entered, with the latest handoff taking precedence.
After an idea becomes a project, that conversation is read under the project's name and identity.

**Screen** (second row of the mockup): the top-right button says only "3 waiting on you · 10 today". The panel it opens lists what is waiting on you and what is in progress, one line each;
shelved and closed are merged into one line that expands only when tapped. Ten things or twenty, it is the same: the panel grows only in "Waiting on you" and "In progress", and everything else is always one line.

**Clearing (closing)**
- **Closes itself**: when the thing is no longer waiting on anyone (the decision was made, the reminder was set, the question was answered), Muninn marks it closed and mentions it in passing in the conversation
  in one sentence (if wrong, the user says so and it is changed back). On every later round the user speaks, Muninn re-judges whether any question is still waiting on them.
- **Closed by saying so**: "The dentist thing is done", "forget the phone for now", "leave all of these for now". Muninn decides which thing (or things) it is, and closes or shelves it.
- **Left long enough, it shelves itself**: anything not closed, untouched for a week, and not waiting on the user becomes shelved; Muninn mentions it once and not again.
- Closed things are still visible that day ("3 closed today"), and are gone from that line the next day; to find one, go through "Details" or just ask Muninn.

**Reminders**
- **When the user pauses**: after a stretch with no messages (say 20 minutes), and with something waiting on the user, Muninn says that one question in that thing's conversation.
  At most once per pause, the same thing once a day, never during do-not-disturb hours; questions left by deep talks are not raised here (they are asked on returning to the deep talk).
- **At the end of the day**: the evening summary lists what is not closed, one sentence of status each, and the user can reply directly, "Log Acme as an interview, the rest tomorrow".
- **The next morning**: unclosed things carry over into today's line, marked "yesterday's".
- Things with a deadline follow their existing deadline reminders; the same thing is not repeated by two kinds of reminder.

## Long conversations: how to compress

This is a conversation that never ends, and it quickly exceeds what a model can read at once. The approach follows the results of a compression experiment done in another project:

- **The original text is always kept; summaries are only pointers**. In the experiment, every lost detail was lost because the only copy of it was in a summary. So every message's original text
  is stored in the database and never deleted (the current conversation gets trimmed; that has to change); a summary is only responsible for pointing at "which thing, which segments".
- **Each topic has its own "current state", not one summary for the whole conversation**. The current state records: what was decided, what is still waited on, what the next step is, and
  which segments hold the related original text. In the experiment, asking the summary to be "current state plus index" raised the details kept from 31 to 70 (out of 72).
- **Which original text to fetch is decided at read time**. A summary drops whatever the current goal does not need, because when it is written nobody knows what will be talked about next;
  fetching original text once that is known recovers almost everything (28–32 of 32 recovered), and the cheapest model picks well. For Muninn,
  "what will be talked about next" is exactly the topic `turn_topic` decides: on switching to that topic, read its current state, then fetch its most relevant original text.
- **No summaries of summaries**. The more times a detail passes through a summary, the more likely it is lost (one pass keeps everything, four passes lose everything). A topic's current state is updated each time
  from the previous current state plus new original text, and the original text can always be fetched, so anything lost can be found again.
- **The user's own words are kept as original text longer**. In the experiment, "keep the user's words as original text" recovered exactly the things the user had said, at the cost of a longer context;
  the last few of the user's own messages in each topic go straight into the context.
- **A cheap model is enough**. With original text available to fetch, the summary's only job is pointing the way; the cheapest summary model plus fetching original text matches the best one (93 of 96 kept).
  This also makes it doable with a local model in privacy mode.
- **It can be measured**: as in that experiment, plant facts in the evals, ask for them back, and see whether they can still be found after compression; the topic current state (`topic_state`) and
  original-text recall (`topic_recall`) are registered as decision points and evaluated with the other decision points.

What makes up the context each round: the user's message, Muninn's fixed instructions, the current topic's current state and its last few original messages, a few fetched segments of original text, and one line for each other
topic not yet closed (so Muninn knows what else is open, and can judge whether the user is talking about one of them).

Implementation (`core/topic_memory.py`): the last 12 messages are read as original text; messages that leave those 12 are folded by a scheduled job into that topic's
current state once 8 have accumulated (`topic_state`: what was decided, what is still waited on, the next step, and which message number each is at); those not yet folded in are read as original text, so nothing is lost if the job runs late.
For the earlier part, the user's own last 6 messages are read as original text, and `topic_recall` picks up to 4 of the rest based on the user's message and fetches them. While the conversation is still short it is read whole,
and neither decision point is asked. Other unclosed topics get only a name and a state; when this round goes to the cloud, only topics that can themselves go to the cloud are included. The current state carries the flags of its sources
(whether it has messages that did not pass the check, whether it has content from a borrowed session), which, like the original text, decide where this round can be sent and which project can be told.

## Who does what

- **Muninn**: the conversation home and sidebar, the `turn_topic` decision point, automatic borrowing and returning of sessions, ideas' `idea_next`, and handing off and reporting back experiments.
- **The external project system**: the interface for upgrading an idea sandbox to a real project (it already has an upgrade script; it needs an endpoint Muninn can call);
  the sandbox's write rules (no longer asking every time) are also changed on its side.

## Stages

- **a. The home screen becomes the conversation**: one conversation, cards in the conversation, other pages moved into the top-right panel (eight pages merged into four); undo becomes spoken (a new undo tool
  that finds the thing the user means among recent actions).
- **b. Automatic topic judgment, automatic borrowing and returning of sessions**: `turn_topic`; topic states, closing and reminders, the one line and its expanded list; all original text kept,
  each topic's current state and original-text recall; `/attach` and take back no longer needed (kept as a fallback).
- **c. Ideas move forward on their own**: `idea_next`, experiment handoff and reporting, upgrading to a project.

## How the tests change

The API contract and the screen-name contract are still enforced. The few user-flow tests written around buttons (the idea's "discuss / shelve for now", `/attach`,
"back to conversation", "Allow hands-on") change meaning: they are rewritten with each stage into the new flows (for example "state an idea → Muninn discusses it →
the experiment result appears in the conversation"). This is an expected rewrite, not a broken safety net.

## Decided (10/24)

1. Experiments in idea sandboxes **no longer ask for every file write and every command**; they ask only to touch things outside the folder, or to reach the network outside the allowlist. The external project system has been asked to change its rules.
2. The home screen is only the conversation; the sidebar and side information move into the panel the top-right button opens; the input box has no topic tag.
3. There is no "Undo" button; undo is spoken.
4. What was talked about today and what is not yet closed is said in one line by the top-right button, which opens to the list (replacing the line above the conversation).
5. Order a → b → c.

## New in this version, waiting for your review

- First design of the four entries (Today, Progress, Lists, Settings); the panel gets a darker background, white cards, and colored group names; the entries become text rows.
- The whole screen has only four things: the conversation, the input box, one button at the top right, and the panel it opens; inside the panel: today's things → one thing's details → the four entries (eight pages merged into four).

- Many things: topics are split into "waiting on you / in progress / shelved / closed"; they close themselves, close by being told, and shelve themselves when left long; reminders when the user pauses, in the evening, and the next morning.
- Long conversations: all original text kept, one current state per topic, original text fetched at read time (following the results of that compression experiment).
