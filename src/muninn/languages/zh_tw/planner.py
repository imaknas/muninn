# today's and this week's priorities, and when each is due (core/planner.py)
TEXTS = {
    "overdue": "逾期 {days} 天",
    "due_today": "今天到期",
    "due_tomorrow": "明天到期",
    "due_on": lambda due: f"{due.month}/{due.day} 到期",
    "finish": "完成",
    "target_step": "{target}:{step}",
    "deadline_why": "{title}({due})",
    "behind": "{goal}落後",
}
