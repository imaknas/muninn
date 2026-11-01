# what Muninn says about the projects' messages, and tells a project about its questions (core/project_messages.py)


def _inbox_title(project, kind):
    return f"{project}:{'處理完了' if kind == 'done' else '進度'}"


TEXTS = {
    "no_answer_needed": "這題不用回答,把它結掉,之後不要再問;真的需要他決定時再當新問題問。",
    "no_project": "沒有叫 {project} 的專案",
    "in_lease_handover": "在深談的交接裡,回到那段深談時問他",
    "inbox_title": _inbox_title,
    "dismissed_why": "你在 Muninn 略過了這個問題",
    "answered_why": "你回答了這個專案問你的問題",
}
