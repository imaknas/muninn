# where the user was (core/places.py)
TEXTS = {
    "elsewhere": "Elsewhere",
    "place_hours": lambda name, hours, detail: f"{name} {hours}h" + (f" ({detail})" if detail else ""),
    "place_separator": "; ",
    "today": "Where you were today: {places}",
}
