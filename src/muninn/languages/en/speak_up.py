# what Muninn says when it speaks up in a pause (core/speak_up.py)
TEXTS = {
    "while_away": lambda items, more: f"While you were away: {items}" + (f", and {more} more reminders" if more else "") + ".",
    "push": "Muninn has a question for you",
}
