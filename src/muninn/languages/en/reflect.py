# Muninn's weekly reflection (core/reflect.py)
from muninn.languages.en.reports import md

TEXTS = {
    "weekly_title": lambda since, last: f"Weekly reflection, {md(since)}–{md(last)}",
    "preview": "{title} (preview)",
    # the reflection's headings, as the model is asked to write them
    "heading_thinking": "What's on your mind this week",
    "heading_promised": "What you said you'd do",
    "heading_decided": "Decisions you made",
    "heading_time": "Where the time went",
    "heading_state": "How you've been",
    "heading_question": "One question",
}
