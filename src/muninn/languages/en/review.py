# the plan review: what each goal and deadline waiting for a yes or no says, and what deciding it did (core/review.py)
from muninn.languages.en.reports import md

UNITS = {"hours": " h", "applications_submitted": " applications", "sleep_hours": " h of sleep", "steps": " steps"}

TEXTS = {
    "amount": lambda period, target, metric: f"{target:g}{UNITS.get(metric, '')} {'a week' if period == 'week' else 'a day'}",
    "no_activity_after": "no activity after {cutoff}",
    "deadline_detail": lambda due, days: f"by {md(due)} ({'1 day' if days == 1 else f'{days} days'} left)",
    "no_such": "There's no {what}",
    "confirmed": "Confirmed: {title}",
    "changed": "Changed and confirmed: {title} → {value}",
    "removed": "Removed: {title}",
}
