# the conversation's own tools (core/tools/conversation.py)
TEXTS = {
    "nothing_today": "Nothing is due or behind today.",
    "no_answer": "The local model isn't answering right now, so I kept the question as a note",
    "mail_unanswered": "Couldn't look through your mail: no model answered. Ask again in a moment",
    "what_answer": "What's the answer?",
    "already_answered": "That question has already been answered",
    "passing_on": "I'll pass it on to {project}: {answer}",
    "no_question": "No question is being answered right now",
    "answered": "Answered: {answer}",
    "options": "You can answer: {options}",
    # how Muninn says it kept a wish (core/requests.py finds the turn it was asked in by it)
    "recorded": "Added to the request list",
    "feature_kept": lambda recorded, n: f"{recorded} ({n} waiting); it will be dealt with next time",
    "nothing_to_undo": "Nothing done lately can be undone",
    "undo_not_found": "Can't find \"{what}\"; what I did lately: {recent}",
    "undo_which": "Which one? {among}",
    "cannot_undo": "\"{what}\" can't be undone",
    "undone": "Undone: {what}",
    "ask_what": "Ask which project, and what?",
    "a_deep_talk": "a deep talk",
    "not_passed_on": ("This conversation holds what {source} brought back, and what it read outside (web pages and the like) "
                      "must not steer other projects through me, so what is said here is not passed on to {project}. "
                      "To reach {project}, say it again in a conversation without {source}'s content."),
    "asked": "Asked {project}; its answer will show up here",
    "which_project": "Which project?",
    "lent": "Over to {project}",
}
