# what Muninn says about the projects' messages, and tells a project about its questions (core/project_messages.py)


def _inbox_title(project, kind):
    return f"{project}:{'處理完了' if kind == 'done' else '進度'}"


def _claim_wrong(project, answer):
    said = f"記成你決定了:{answer}" if answer else "說你在 session 裡決定了"
    return f"{project} {said};你說不對,等你的答案"


TEXTS = {
    "no_answer_needed": "這題不用回答,把它結掉,之後不要再問;真的需要他決定時再當新問題問。",
    "no_project": "沒有叫 {project} 的專案",
    "in_lease_handover": "在深談的交接裡,回到那段深談時問他",
    "inbox_title": _inbox_title,
    "dismissed_why": "你在 Muninn 略過了這個問題",
    "answered_why": "你回答了這個專案問你的問題",
    "claim_said": "{project} 說你在 session 裡決定了:{answer}",
    "claim_wrong": _claim_wrong,
    "claim_check": "{project} 說你對「{text}」決定了:{answer}。對嗎?",
    "claim_check_no_answer": "{project} 說你在 session 裡決定了「{text}」,但沒說答案。對嗎?",
    "claim_right": "對",
    "claim_not_right": "不對",
    "confirmed_why": "他確認了你記下的答案",
    "recheck": "你記成他決定了「{answer}」,但他說不是;請檢查照那個答案做了哪些事,改照這次的。",
    "recheck_no_answer": "你說這題他已經在 session 裡決定了,但他說沒有;請檢查當成已決定之後做了哪些事,改照這次的。",
}
