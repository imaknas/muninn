# what the task registry says to the user and tells the projects that asked (core/tasks.py)


def _already_settled(day, status, n):
    return f"這件在 {day} {'已經完成' if status == 'done' else '已決定不做'}(T-{n})"


TEXTS = {
    "replaced_asked": "「{old}」後來換成了「{new}」,「{new}」{how}。「{old}」呢?",
    "replaced_done": "已經做完了",
    "replaced_dropped": "不做了",
    "replaced_answer_done": "也做完了",
    "replaced_answer_dropped": "不用做了",
    "replaced_answer_keep": "還要做",
    "not_needed_by": "{project} 不需要了",
    "merged": "「{title}」跟 T-{n} 是同一件,合在一起",
    "already_settled": _already_settled,
    "already_settled_why": "重送了一件已完成的事;真的是新的,請帶 new_reason 再送",
    "due_moved": "他把這件的期限改到 {due}",
    "reopened": "撤回了先前的完成",
}
