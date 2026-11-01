# asking which goal time away from the keyboard counts toward (core/offline_time.py)
TEXTS = {
    "not_counted": "Doesn't count (a break, personal)",
    "duration": lambda minutes: f"{minutes:.0f} min" if minutes < 90 else f"{minutes / 60:.1f} h",
    "focus_said": "“{name}” was on for {duration} just now",
    "gap_said": "The computer sat idle {start}–{end} ({duration})",
    "focus_ask": "{said}. Which goal does it count toward?",
    "gap_ask": "{said}. What were you doing? Which goal does it count toward?",
    "logged": "Counted toward “{goal}”: {said}",
}
