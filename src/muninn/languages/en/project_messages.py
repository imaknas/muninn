# what Muninn says about the projects' messages, and tells a project about its questions (core/project_messages.py)


def _inbox_title(project, kind):
    return f"{project}: {'done' if kind == 'done' else 'progress'}"


def _claim_wrong(project, answer):
    said = f"had you deciding: {answer}" if answer else "says you decided it in its session"
    return f"{project} {said}; you said that's wrong, so it waits for your answer"


TEXTS = {
    "no_answer_needed": "This question needs no answer: close it and don't ask it again; if a decision from the user is really needed, ask it as a new question.",
    "no_project": "There is no project called {project}",
    "in_lease_handover": "In the deep talk's handover; asked when that deep talk is picked up again",
    "inbox_title": _inbox_title,
    "dismissed_why": "You skipped this question in Muninn",
    "answered_why": "You answered the question this project asked you",
    "claim_said": "{project} says you decided it in its session: {answer}",
    "claim_wrong": _claim_wrong,
    "claim_check": "{project} says you decided \"{text}\": {answer}. Is that right?",
    "claim_check_no_answer": "{project} says you decided \"{text}\" in its session, without saying what. Is that right?",
    "claim_right": "Yes",
    "claim_not_right": "No",
    "confirmed_why": "The user confirmed the answer you recorded",
    "recheck": "You recorded the user deciding \"{answer}\", but they say they didn't; check what you did on that answer and go by this one.",
    "recheck_no_answer": "You said the user decided this in your session, but they say they didn't; check what you did as if it were decided and go by this one.",
}
