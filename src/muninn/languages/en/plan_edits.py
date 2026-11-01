# what a change to a plan says it changes, and why one does not fit (core/plan_edits.py)


def _goal_target(title, period, target, now):
    now = f"{now:g}" if isinstance(now, (int, float)) else now
    return f"Change \"{title}\" to {target:g} a {'week' if period == 'week' else 'day'} (now {now})"


TEXTS = {
    "goal_target": _goal_target,
    "deadline_due": "Move the deadline of \"{title}\" to {due} (now {now})",
    "add_target": "Add \"{company} {role}\" to the applications (priority {priority})",
    "remove_target": "Take \"{title}\" off the applications",
    "restore_target": "Put the removed application back",
    "remove_entry": "Take \"{title}\" out of the plan",
    "restore_entry": "Put \"{title}\" back in the plan",
    "no_goal": "There is no goal {goal}",
    "no_deadline": "There is no deadline {deadline}",
    "already_there": "{id} is already there",
    "not_there": "There is no {id}",
    "cannot_remove": "Can't remove anything from {section}",
    "unknown_edit": "That isn't a change I know how to make",
}
