# what Muninn says about the requests it was asked for, as each goes live or is rolled back (core/requests.py)


def _rolled_back(reason, text):
    return f"上線檢查沒過{f'({reason})' if reason else ''},已經退回上一版:「{text}」還沒上線。"


TEXTS = {
    "recorded": "記到需求清單了",
    "live": "上線了:「{text}」",
    "not_yet": "還沒好",
    "rolled_back": _rolled_back,
    "push_rolled_back": "Muninn 退回上一版了",
    "push_updated": "Muninn 更新了",
}
