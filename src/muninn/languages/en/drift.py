# what the drift rules say about a plan that is off track (core/drift.py)
from muninn.languages.en.reports import md


def _days(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


TEXTS = {
    "minutes": "{value:.0f} min",
    "steps": "{value:,.0f} steps",
    "at_most": "{goal}: {actual} so far (limit {target})",
    "this_week": "{goal}: {actual}/{target} this week",
    "today": "{goal}: {actual}/{target} today",
    "on_pace": "On pace you'd be at {expected} by now",
    "overdue": lambda title, days, due: f"{title}: {_days(days)} overdue ({md(due)})",
    "due_today": "{title}: due today",
    "days_left": lambda title, days, due: f"{title}: {_days(days)} left ({md(due)})",
    "finish_this": "finish it",
    "target_step": "{target}: {step}",
    "mark_done": "When it's done, mark it done on Muninn's Today page",
    "to_go": lambda n: f"{n} still to go",
    "todos_overdue": lambda n: "1 to-do overdue" if n == 1 else f"{n} to-dos overdue",
    "late_night": "Working late at night: until {time}",
    "stop_earlier": "Stop earlier today, and move what you do late at night to the daytime",
    "idle": lambda domain, days: f"{domain}: nothing for {_days(days)}",
}
