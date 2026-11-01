# where the user was (core/places.py)
TEXTS = {
    "elsewhere": "其他地方",
    "place_hours": lambda name, hours, detail: f"{name} {hours}h" + (f"({detail})" if detail else ""),
    "place_separator": ";",
    "today": "今天的位置:{places}",
}
