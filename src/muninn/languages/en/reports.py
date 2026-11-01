# the morning report, the evening review and the weekly report (core/reports.py)
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def md(d) -> str:  # a date as English writes the month and day: "Oct 30"
    return f"{MONTHS[d.month - 1]} {d.day}"


def _days(n: int) -> str:
    return "1 day" if n == 1 else f"{n} days"


TEXTS = {
    "no_data": "no data yet",
    "goal_unknown": "{goal}: no data yet",
    "goal_at_most": "{goal}: {actual} (limit {target})",
    "goal_this_week": "{goal}: {actual}/{target} this week{pace}",
    "goal_today": "{goal}: {actual}/{target} today{pace}",
    "pace": " (on pace: {expected})",
    "kept": "kept",
    "over": "over",
    "met": "met",
    "behind": "behind by {amount}",
    "on_pace": "on pace",
    "do_first": "Do first today:",
    "nothing_due": "Nothing is due or behind today.",
    "priority": "{n}. [{domain}] {text} ({why})",
    "project_todos": lambda n: (f"Your projects have {n} more thing only you can do (in the app)" if n == 1
                                else f"Your projects have {n} more things only you can do (in the app)"),
    "section": "— {title} —",
    "week_item": "• {text} ({why})",
    "brief_title": lambda day: f"Morning report, {WEEKDAYS[day.weekday()]} {md(day)}",
    "push_first": "Morning report · First: {text}",
    "push_nothing": "Morning report · Nothing due or behind today",
    "applied": "• Applied: {targets}",
    "week_hours": "• {goal}: {today:.1f}h today, {line}",
    "activity": "• Activity observed: {hours:.1f}h",
    "not_pushed": "• (not pushed) {title}",
    "tomorrow_first": "Do first tomorrow:",
    "evening_title": lambda day: f"Evening review, {md(day)}",
    "deadline": lambda title, due, days: f"• Deadline: {title} ({md(due)}, {_days(days)} left)",
    "filters": "Filters:",
    "pivot": "Pivot ({date}): if {cond}, then {then}",
    "decision_tree": "Decision tree:",
    "weekly_title": lambda week_start: f"Weekly report, week of {md(week_start)}",
}
