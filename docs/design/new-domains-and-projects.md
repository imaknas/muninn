# Creating new domains, onboarding new users, project-side interfaces (design)

Status: decided; work starts in order (2026-10-24). Merged after discussion across three sessions.

## The problem

1. **New domains cannot be created**: a domain (job search, PhD, health…) is `data/domains/<id>/plan.yaml` and can only be written by hand. A brand-new user has no way to start; an idea cannot be upgraded into a domain either.
2. **The project half depends on an external system**: handoffs, project questions and tasks, and deep talks all rely on a separate system that manages projects. Users who have only Muninn cannot use any of these.

## A. Creating a domain: one general capability

- **Plans actor**: responsible for changing plans (today's change_plan), plus creating (create) and archiving (archive). Tools: change_plan, propose_domain.
  - PlanBase stays read-only, readable by every actor; PlanReview stays its own actor.
- **Goes through the action gate, following the existing rule table, not hardcoded**:
  - The user says "Start an X domain for me" → create it directly, with undo.
  - The model infers from the conversation that one should be created → ask first.
- **Draft**: the model drafts it from the material at hand (this conversation, the idea's material, an exploration's handoff).
  - Every goal, deadline and job is "to be confirmed", with what it is based on written down; once created, plan review asks about them one by one.
  - Onboarding creates several domains at once, so there must be a way to confirm "this whole domain as drafted" in one go.
- **Things decided at creation time**:
  - Sensitivity: sensitive by default, relaxed only when the user says so. It only matters in privacy mode (sensitive content goes only to local models); you currently have everything set to the cloud, so it has no effect.
  - Attribution rules: which repos, places and apps count toward this domain; without them its hours are always 0.
  - Weekly time budget: show which existing goals it will compete with for time. This is what Muninn adds over just opening a session.
- **Undo in two stages**: right after creation it can be fully undone; once records have been attributed to it, time slots scheduled or application packets prepared, it becomes "archive the domain" and the data is kept.
- **Origin**: when upgraded from an idea, the creation result carries `idea:<n>`, the idea is marked as upgraded, and undo restores both. The idea side does not touch the plan directly; the wiring code connects them.

## B. Onboarding new users: a conversation flow built on A

- With no domains at all, the home screen, Today, Progress and the input box must all show proper empty states and guide onboarding. First write a browser test for "nothing breaks without any domain".
- The first conversation asks: what the user cares about lately, whether there are deadlines, what counts as progress; it drafts a few domains and hands them to A.
- At the same time, the existing setup cards walk the user through connecting accounts and turning on notifications.
- What the user answers is personal data and follows the same "may it go to the cloud" setting as everything else (everything to the cloud, or in privacy mode only non-sensitive content goes to the cloud); it is not handled differently because it is onboarding.

## C. Ideas

- Upgrading to a domain = calling A with the idea's material.
- Add the "decide" step: shelve, abandon. Otherwise ideas only come in and never go out.
- Exploration goes through D's Sandboxes: what gets opened is a project, which is then discussed in deep talks like any other project; deep talks are not lent to "things that are not projects".

## D. Project side: a few small interfaces, two implementations

Split by "who uses it, and which implementation may provide only part of it" (the same reasoning as splitting storage into small ports):

| Interface | What it does | Exists now? |
|---|---|---|
| ProjectRecords | which projects exist, each one's to-dos and status | yes |
| HandoffOutbox | sends pending handoffs | new |
| ProjectInbox | receives a project's questions, progress and tasks | new (currently POST /api/messages) |
| SessionRunner | deep talks | yes |
| Sandboxes | opens a project for exploring an idea | new |

The wiring code picks the implementations from the settings; the core only sees the pieces it uses.

**Implementation one: an external project management system** (the current approach). Names are written generically (records system, session runner), with no external tool's name or path. Handoffs work as before: they wait for the other side to pull them.

**Implementation two: a built-in local version** (so people who have only Muninn can use it too):
- Projects: folders allowed in the settings (not everything under workspace_roots may be lent; in privacy mode work projects cannot be sent to the cloud).
- To-dos, status: TODO.md / STATE.md in the folder.
- Handing off: written into `<project>/.muninn/inbox.md`; receiving: read from `<project>/.muninn/outbox.yaml` (format in contracts/local-projects.v1.md).
- That project's agent has to know to read the inbox: this is written in the docs and the user adds it themselves; Muninn does not modify other projects' CLAUDE.md.
- Deep sessions: done last. Not just running `claude -p` once; it needs the same contract: permission cards, idle reclaim, quota, handoff summary, and real write-blocking settings (writing it only in the prompt is not enough).
- The formats of both implementations (to-dos, status, handoffs, inbox/outbox) all go into `contracts/` and run the same set of contract tests.
- Two levels of read/write: by default, read-only discussion; writing files and running tools is a separate level that needs the user's permission.

## Order and division of work

1. **A** (the architecture diagram session): Plans actor, PlanRepo.create/archive, DomainCreated.
2. **propose_domain, idea upgrade/shelve/abandon, exploration connected to Sandboxes** (the session handling new commands), after A.
3. **B onboarding** (the desktop version session): empty states, the onboarding conversation, UI.
4. **D** (can run in parallel with 1–3; settle ProjectInbox's return path first):
   - split the interfaces and wrap the current approach as implementation one (architecture diagram session);
   - the built-in local version's records, outbox and inbox (desktop version session); the deep talk runner last.

## Jobs: check fit before preparing an application packet (decided 10/24)

Neither "fully automatic" nor "ask first for every company":
- Before preparing an application packet, compare the original job posting against the user's résumé and the plan's requirements (basic requirements, employment type, location, salary band).
- **Clearly fits** → prepare it automatically as now; the user only has to press "Sent".
- **Unsure or does not fit** → first show a question card stating which requirement does not match; prepare it only if the user says yes; if they say no, never ask about that company again.
- The user's answers (yes to an unsure one, "not applying" to an automatically prepared one) are kept as eval cases, so it judges better later.
- Jobs do not go through plan review separately; the list stays as it is, and the filtering happens "before preparing".
- Who does it: the desktop version session (the packet actor); it does not need to wait for A.
