# finding room on the calendar (core/schedule.py)
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

TEXTS = {
    "no_room": lambda day, minutes: f"No free {minutes} minutes on {MONTHS[day.month - 1]} {day.day}",
}
