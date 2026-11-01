# lending a conversation to a project's session, and the permission requests it makes (core/lease.py)

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _talked_until(project, at):
    return f"Talked with {project} until {MONTHS[at.month - 1]} {at.day}, {at:%H:%M}"


def _heard(said):
    return " ".join(x if x[-1:] in ".!?" else f"{x}." for x in said)


def _saved_files(project, n):
    return f"Saved to {project} ({n} file{'' if n == 1 else 's'})"


TEXTS = {
    # why the runner refused, by its category
    "not_registered": "This project can't be lent to (it isn't in the registry).",
    "cap_reached": "As many conversations as allowed are lent out already; take one back first.",
    "busy": "It's still busy there (a terminal is open, or it's writing a handover); close the terminal and try again.",
    "not_found": "That lease can't be found.",
    "agent_login": "The project's session isn't signed in; sign in again on that computer first.",
    "rate_limit": "The usage limit is used up; talk again once it resets.",
    "unreachable": "Can't reach the service that runs the sessions.",
    "work_not_allowed": "This project can't go hands-on now (the project registry doesn't allow it, or its sandbox isn't ready yet).",
    "runtime_unsupported": "The computer that runs the sessions can't start a deep talk in its current environment.",
    "trouble": "Something went wrong there ({category}).",
    # what a switch of mode is noted as in the talk
    "mode_work": "Hands-on allowed: from now on it asks you before it changes a file or runs a command (what you allowed before must be allowed again)",
    "mode_discuss": "Back to read-only",
    # the user's side
    "quota_used": "Usage so far: {used}",
    "quota_window": "{window} {percent}%",
    "quota_confirm": "{warning}. Start anyway?",
    "already_lent": "This conversation is already lent out; take it back first",
    "not_attachable": "This conversation can't be lent to \"{project}\"; it can go to: {allowed}",
    "none_reachable": "(can't reach the service that runs them)",
    "handed_over": "Handed what we just talked about to {project}; it carries on from there",
    "handed_over_files": "Handed what we just talked about and the files to {project}; it carries on from there",
    "rejoin_after_handover": "Back to {project}: it answers you once it has written the handover of the last talk.",
    "file_gone": "one of the files is gone",
    "handover_stuck": "its handover was never finished",
    "not_rejoined": "Couldn't go back to {project} ({why}); say it again if you want to.",
    "not_lent": "This conversation isn't lent out",
    "in_terminal": "This conversation is open in a terminal; press \"Take back\" first",
    "taking_back": "Taking it back; wait for the handover",
    "sent_files": "(sent {names})",
    "talk_gone": "That deep talk is gone; this conversation is back with Muninn.",
    "which_mode": "Hands-on or read-only?",
    "mode_busy": "It's answering or waiting for your reply; switch once this turn is over.",
    # the runner's side
    "not_resumed": "Couldn't pick up the last talk ({detail}); starting a new one.",
    "a_file": "file",
    "saved_files": _saved_files,
    "talk_lost": "The deep talk with {project} broke off (that session is gone there); the conversation is back with Muninn.",
    "talked_until": _talked_until,
    # what a talk is told
    "files_for_you": "The user sent you these files.",
    "left_undecided": "Your last handover left these undecided; ask the user about them directly as you talk:\n{questions}",
    "said_by_user": "User",
    "said_line": "{who}: {text}",
    "back_since": "The user is back to carry on with you. Since your last talk, they also said this to Muninn:\n\n{said}\n\nCarry on from here.",
    "take_over": "The user just talked this far with Muninn; now you take over and actually look into it and try it out:\n\n{said}\n\n"
                 "Carry on from here; first say how you plan to try it.",
    "heard": _heard,
    "relayed_alone": "(Muninn just answered the user on the side; the part that concerns you: {heard} Just so you know; no need to reply.)",
    "relayed_before": "(Muninn just answered the user on the side; the part that concerns you: {heard})",
    # what Muninn keeps of what it said beside a talk
    "told": "Told {project}: {text}",
    "not_yet_told": "Didn't get to tell {project}: {text}",
    "said_beside": "While you talked with {project}, I said on the side:\n{lines}",
    # the line that says the conversation was handed over
    "lent_resumed": "Back to the talk with {project}: what you say next goes straight to it. "
                    "When you're done or move on to something else, it hands the conversation back by itself.",
    "lent_work": "Opened {project} to try it out: what you say next goes straight to it (it can change files in its own folder; "
                 "it asks you before running a command, and the kinds you've allowed run on their own after that). "
                 "When you're done or move on to something else, it hands the conversation back by itself.",
    "lent_discuss": "What you say next goes straight to {project} (it only reads, looks things up and asks you; it won't change anything). "
                    "When you're done or move on to something else, it hands the conversation back by itself.",
    # a talk's handover, as it joins the conversation
    "handover_none": "The deep talk with {project} is over (no handover).",
    "handover_failed": "The deep talk with {project} is over, but its handover wasn't written; go back to it and ask again if you want.",
    "handover_over": "The deep talk with {project} is over.",
    "handover_decided": "Decided: {items}",
    "handover_open": "Still open: {items}",
    "handover_tasks": "To-dos for you: {items}",
    "listed": lambda items: "; ".join(items),
    # a permission request's push
    "a_project": "A project",
    "push_ask": "{project} asks you something",
    "push_write": "{project} wants to change a file",
    "push_run": "{project} wants to run a command",
    "push_web": "{project} wants to open a web page",
    "push_other": "{project} wants to do something",
    # deciding one
    "already_decided": "This request has been decided already",
    "commands_each": "Every command is asked about; you can only allow this one",
    "session_web_files": "Only web pages and file changes can be allowed for the whole conversation",
    "open_site": "Anyone can write to this site, so it can't be allowed for the whole conversation; only this once",
    "pick_option": "Pick an option to answer the question",
    # what became of one
    "ask_unanswered": "Didn't answer this question",
    "ask_declined": "Didn't pick an option; let it ask in words",
    "ask_chose": "You chose: {answer}",
    "ask_pair": "{question} → {answer}",
    "write_timeout": "No answer, so it didn't change {what}",
    "write_allowed": "Allowed changing {what} once",
    "write_allowed_session": "Allowed changing files in this project's folder for this whole deep talk",
    "write_denied": "Refused changing {what}",
    "run_timeout": "No answer, so it didn't run: {what}",
    "run_auto": "Ran without asking: {what} (you've always allowed this kind)",
    "run_allowed": "Allowed running: {what}",
    "run_denied": "Refused running: {what}",
    "this_thing": "this",
    "other_timeout": "No answer, so it didn't do: {what}",
    "other_allowed": "Allowed: {what}",
    "other_denied": "Refused: {what}",
    "this_page": "this page",
    "web_timeout": "No answer, so it didn't open {host}",
    "web_allowed": "Allowed opening {host} once",
    "web_allowed_session": "Allowed {host} for this whole conversation",
    "web_denied": "Refused opening {host}",
}
