# what the records Muninn tells the projects say for a person to read; their protocol fields stay as they are
# (core/handoff_records.py)


def _said_by(by, why):
    return f"{by} 說的" + (f":{why}" if why else "")


def _withdrawn(reason):
    return {"settled": "在專案那邊決定了", "not_needed": "不需要了"}.get(reason, "不需要了")


TEXTS = {
    "withdrawn": _withdrawn,
    "answered": "已回答",
    "dismissed_note": "他說不用回答",
    "said_by": _said_by,
    "muninn_judged": "Muninn 的判斷",
}
