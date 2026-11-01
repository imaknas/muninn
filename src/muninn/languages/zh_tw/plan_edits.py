# what a change to a plan says it changes, and why one does not fit (core/plan_edits.py)


def _goal_target(title, period, target, now):
    return f"把「{title}」改成{'每週' if period == 'week' else '每天'} {target:g}(目前 {now})"


TEXTS = {
    "goal_target": _goal_target,
    "deadline_due": "把「{title}」的期限改成 {due}(目前 {now})",
    "add_target": "新增投遞目標「{company} {role}」(優先序 {priority})",
    "remove_target": "把「{title}」從投遞清單拿掉",
    "restore_target": "把拿掉的投遞目標放回去",
    "remove_entry": "把「{title}」從計畫拿掉",
    "restore_entry": "把「{title}」放回計畫",
    "no_goal": "沒有目標 {goal}",
    "no_deadline": "沒有期限 {deadline}",
    "already_there": "已經有 {id}",
    "not_there": "沒有 {id}",
    "cannot_remove": "不能刪 {section}",
    "unknown_edit": "不認得的改動",
}
