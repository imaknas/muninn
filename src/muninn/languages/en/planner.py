# today's and this week's priorities, and when each is due (core/planner.py)
from muninn.languages.en.reports import md

TEXTS = {
    "overdue": lambda days: "1 day overdue" if days == 1 else f"{days} days overdue",
    "due_today": "due today",
    "due_tomorrow": "due tomorrow",
    "due_on": lambda due: f"due {md(due)}",
    "finish": "finish",
    "target_step": "{target}: {step}",
    "deadline_why": "{title}, {due}",
    "behind": "{goal} behind",
}
