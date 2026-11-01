# what the engine says: where a repo belongs, an expired connection, what waits in the morning and the evening (core/engine.py)
TEXTS = {
    "which_domain": "Which domain does the activity in the repo “{repo}” belong to?",
    "suggested": " (the local model suggests {domain})",
    "relink_title": "{name}'s access has expired; tap to reconnect it in the app",
    "relink_body": "Until it's reconnected, nothing goes on your calendar and no mail is read.",
    "morning_push": lambda n, first: (f"Good morning · 1 thing is waiting on you (first: {first})" if n == 1
                                      else f"Good morning · {n} things are waiting on you (first: {first})"),
    "waiting_on_you": "Waiting on you: {items}",
    "still_waiting": "Still waiting on you: {items}",
    "no_domain": "There's no domain {domain}",
}
