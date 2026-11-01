# what the things Muninn does say they did, and what it asks before doing one (core/actions.py)


def _task_closed(status, n, title):
    return f"{'Done' if status == 'done' else 'No longer needed'}: T-{n} {title}"


def _when(day, start, end):
    return f"{day:%b} {day.day}, {start:%H:%M}" + (f"–{end:%H:%M}" if end else "")


def _alerts_seen(title):
    return f"Seen: {title or 'reminders'}"


def _ask_settled(question, why):
    return f"No longer waiting on you for \"{question}\": {why}" if question else f"No longer waiting on you for that question: {why}"


def _idea_captured(text, called):
    return f"Idea kept: {text}" + (f" (called {called})" if called else "")


def _idea_settled(text, to):
    return f"Idea \"{text}\" " + {"shelved": "shelved", "dropped": "dropped", "open": "taken back up"}.get(to, to)


def _domain_reviewed(title, n):
    return f"\"{title}\" taken as drafted: {n} item{'' if n == 1 else 's'} confirmed"


TEXTS = {
    "task_closed": _task_closed,
    "task_what": "T-{n} \"{title}\"",
    "task_ask_done": "Is {what} done?",
    "task_ask_done_yes": "Done, close it",
    "task_ask_done_no": "Not yet",
    "task_ask_still": "Do you still need to do {what}?",
    "task_ask_still_yes": "No, close it",
    "task_ask_still_no": "Still to do",
    "task_due": "T-{n} moved to {due}",
    "tracker_status_because": "{because}; status set to {label}",
    "tracker_status": "Set the status of \"{target}\" to {label}",
    "no_goal": "There is no goal {goal}",
    "deadline_done": "Done: {title}",
    "when": _when,
    "block_put": "On the calendar {when}: {title}",
    "block_put_note": "Scheduled by you in Muninn.",
    "block_moving": "Move {title} to {date} {at}",
    "block_moved": "{title} moved to {when}",
    "block_moved_note": "Moved here by you in Muninn.",
    "block_removed": "Cancelled: {title}",
    "prepare_packet": "Still prepare the application packet for \"{title}\"",
    "packet_prepared": "Application packet ready for {title}",
    "packet_set_aside": "Not applying for now: {title}",
    "reply_set_aside": "No need to handle: {title}",
    "packet_apply": "Send the application: {title}",
    "packet_apply_ask": "The application form for {title} is filled in. Send it once you have looked it over?",
    "packet_apply_yes": "Send",
    "packet_apply_no": "Not yet",
    "packet_applied": lambda title, said: f"Sent: {title}, marked as applied" + (f" (the site said: {said})" if said else ""),
    "packet_submitted": "Sent: {title}, marked as applied",
    "reply_handled": "Handled: {title}",
    "place_naming": "Remember this place as \"{place}\"",
    "place_named": "Remembered: {place}",
    "alerts_seen": _alerts_seen,
    "ask_settling": "No longer waiting on you: {why}",
    "ask_settled": _ask_settled,
    "idea_capturing": "Keep this idea: {text}",
    "idea_captured": _idea_captured,
    "idea_settled": _idea_settled,
    "domain_creating": "Create the domain \"{title}\"",
    "domain_archiving": "Archive the domain \"{title}\" (its data stays)",
    "domain_reviewed": _domain_reviewed,
    "sources_assigned": "{sources} count toward \"{title}\"",
    "request_live_preview": "\"{text}\" is live (the running version includes {commit})",
    "request_live": "Marked live: {text} (going by commit {commit})",
    "ask": "{text}?",
    "yes": "Yes",
    "no": "No",
}
