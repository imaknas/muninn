# what the task registry says to the user and tells the projects that asked (core/tasks.py)


def _already_settled(day, status, n):
    return f"This was {'done' if status == 'done' else 'decided against'} on {day} (T-{n})"


TEXTS = {
    "replaced_asked": "\"{old}\" was replaced by \"{new}\", which {how}. What about \"{old}\"?",
    "replaced_done": "is done",
    "replaced_dropped": "was dropped",
    "replaced_withdrawn": "is no longer asked for",
    "replaced_together": "\"{old}\" was raised again as \"{new}\", which is still to do. What about \"{old}\"?",
    "replaced_alone": "\"{old}\" was replaced by another to-do. What about \"{old}\"?",
    "replaced_same": "the same as \"{new}\"",
    "replaced_answer_same": "Same as the new one",
    "replaced_answer_done": "Already done",
    "replaced_answer_dropped": "Not needed",
    "replaced_answer_keep": "Still to do",
    "not_needed_by": "{project} no longer needs it",
    "merged": "\"{title}\" is the same as T-{n}; merged",
    "already_settled_why": "This resends something already settled; if it really is new, send it again with new_reason",
    "already_settled": _already_settled,
    "due_moved": "The user moved its deadline to {due}",
    "reopened": "The earlier completion was undone",
}
