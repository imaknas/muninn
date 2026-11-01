# what Muninn says about the requests it was asked for, as each goes live or is rolled back (core/requests.py)


def _rolled_back(reason, text):
    return f"The check after going live failed{f' ({reason})' if reason else ''}, so Muninn went back to the previous version: \"{text}\" isn't live yet."


TEXTS = {
    "recorded": "Added to the request list",
    "live": "Live now: \"{text}\"",
    "not_yet": "Not working yet",
    "rolled_back": _rolled_back,
    "push_rolled_back": "Muninn went back to the previous version",
    "push_updated": "Muninn was updated",
}
