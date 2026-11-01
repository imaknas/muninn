# the plan review: what each goal and deadline waiting for a yes or no says, and what deciding it did (core/review.py)
UNITS = {"hours": " 小時", "applications_submitted": " 家", "sleep_hours": " 小時", "steps": " 步"}

TEXTS = {
    "amount": lambda period, target, metric: f"{'每週' if period == 'week' else '每天'} {target:g}{UNITS.get(metric, '')}",
    "no_activity_after": "{cutoff} 之後不活動",
    "deadline_detail": lambda due, days: f"{due.month}/{due.day} 前(剩 {days} 天)",
    "no_such": "沒有 {what}",
    "confirmed": "確認:{title}",
    "changed": "改好並確認:{title} → {value}",
    "removed": "拿掉了:{title}",
}
