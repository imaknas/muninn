# what the records Muninn tells the projects say for a person to read; their protocol fields stay as they are
# (core/handoff_records.py)


def _said_by(by, why):
    return f"{by} 說的" + (f":{why}" if why else "")


TEXTS = {
    "dismissed_note": "他說不用回答",
    "said_by": _said_by,
    "muninn_judged": "Muninn 的判斷",
}
