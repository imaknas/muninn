# what the records Muninn tells the projects say for a person to read; their protocol fields stay as they are
# (core/handoff_records.py)


def _said_by(by, why):
    return f"{by} said so" + (f": {why}" if why else "")


TEXTS = {
    "dismissed_note": "The user said it needs no answer",
    "said_by": _said_by,
    "muninn_judged": "Muninn's judgment",
}
