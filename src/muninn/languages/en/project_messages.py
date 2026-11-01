# what Muninn says about the projects' messages, and tells a project about its questions (core/project_messages.py)


def _inbox_title(project, kind):
    return f"{project}: {'done' if kind == 'done' else 'progress'}"


TEXTS = {
    "no_answer_needed": "This question needs no answer: close it and don't ask it again; if a decision from the user is really needed, ask it as a new question.",
    "no_project": "There is no project called {project}",
    "in_lease_handover": "In the deep talk's handover; asked when that deep talk is picked up again",
    "inbox_title": _inbox_title,
    "dismissed_why": "You skipped this question in Muninn",
    "answered_why": "You answered the question this project asked you",
}
