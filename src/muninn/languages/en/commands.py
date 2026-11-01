# what the command runner says itself (core/commands.py)
TEXTS = {
    # in the routing prompt (English for every language): how short a button label and a topic title are
    "label_length": "at most 4 words",
    "title_length": "at most 5 words",
    "sent_files": "(sent {names})",
    "login_lost": "The sign-in to {who} has expired, so I kept this as a note; sign in again and say it once more.",
    "no_model": "No model is connected yet, so I kept this as a note; connect one on the Settings page and I'll understand what you say.",
    "photos": "photos",
    "pdfs": "PDFs",
    "files_local_only": "Your privacy settings let only the local model see {kinds}, and the local model can't read them, so I kept this as a note; switch to a model that can, or say it in words.",
    "files_unreadable": "The model set up now can't read {kinds}, so I kept this as a note; switch to a model that can, or say it in words.",
    "unanswered": "No model answered this time (no reply, or it couldn't be reached), so I kept this as a note; say it again in a moment.",
    "not_understood": "I didn't understand that, so I kept it as a note. Try saying it another way.",
    "not_done": "Not done: {why}",
    "noted": "Noted.",
    "over_budget": "Today's model budget is used up, so I kept this as a note; I'll understand what you say again tomorrow (or once budget.daily_usd is raised).",
}
