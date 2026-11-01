# what the settings page names each kind of work a model is picked for (core/task_models.py)
TEXTS = {
    "label": lambda group: {"route": "Routing", "facts": "News", "answer": "Answers", "draft": "Drafts", "mail": "Mail",
                            "reflect": "Reviews"}[group],
    "detail": lambda group: {"route": "What to do with what you say", "facts": "What your news changes",
                             "answer": "Answering your questions", "draft": "Application packets (and the fit check before one)",
                             "mail": "Sorting new mail and drafting replies",
                             "reflect": "Conversation digests and the weekly review"}[group],
}
