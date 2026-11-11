# what the things Muninn does say they did, and what it asks before doing one (core/actions.py)


def _task_closed(status, n, title):
    return f"{'完成' if status == 'done' else '不用做了'}:T-{n} {title}"


def _when(day, start, end):
    return f"{day.month}/{day.day} {start:%H:%M}" + (f"–{end:%H:%M}" if end else "")


def _alerts_seen(title):
    return f"看過了:{title or '提醒'}"


def _ask_settled(question, why):
    return f"不再等你「{question or '那個問題'}」:{why}"


def _idea_captured(text, called):
    return f"記下這個想法了:{text}" + (f"(叫它 {called})" if called else "")


def _idea_settled(text, to):
    return f"想法「{text}」{ {'shelved': '先擱著', 'dropped': '不要了', 'open': '拿回來'}.get(to, to)}"


TEXTS = {
    "task_closed": _task_closed,
    "task_what": "T-{n}「{title}」",
    "task_ask_done": "{what}做完了嗎?",
    "task_ask_done_yes": "做完了,結案",
    "task_ask_done_no": "還沒",
    "task_ask_done_by": "{by} 說 {what}已經做完了,結案嗎?",
    "task_ask_still": "{what}還要做嗎?",
    "task_ask_still_by": "{by} 說 {what}不用做了,結案嗎?",
    "task_ask_still_yes": "不用了,結案",
    "task_ask_still_no": "還要做",
    "task_due": "T-{n} 改到 {due}",
    "tracker_status_because": "{because},狀態改成{label}",
    "tracker_status": "把「{target}」的狀態改成{label}",
    "no_goal": "找不到目標 {goal}",
    "deadline_done": "完成:{title}",
    "when": _when,
    "block_put": "排進行事曆 {when}:{title}",
    "block_put_note": "由你在 Muninn 排的。",
    "block_moving": "{title} 移到 {date} {at}",
    "block_moved": "{title} 移到 {when}",
    "block_moved_note": "由你在 Muninn 移過來的。",
    "block_removed": "取消了:{title}",
    "prepare_packet": "還要準備「{title}」的投遞包嗎",
    "packet_prepared": "準備好 {title} 的投遞包",
    "packet_set_aside": "先不投:{title}",
    "reply_set_aside": "不用處理:{title}",
    "packet_apply": "送出申請:{title}",
    "packet_apply_ask": "{title} 的申請表都填好了,看過沒問題就送出?",
    "packet_apply_yes": "送出",
    "packet_apply_no": "先不要",
    "packet_applied": lambda title, said: f"送出了:{title},記成已投遞" + (f"(網站說:{said})" if said else ""),
    "packet_submitted": "送出了:{title},記成已投遞",
    "reply_handled": "處理好了:{title}",
    "place_naming": "記住這裡是「{place}」",
    "place_named": "記住了:{place}",
    "alerts_seen": _alerts_seen,
    "ask_settling": "不再等你:{why}",
    "ask_settled": _ask_settled,
    "idea_capturing": "記下這個想法:{text}",
    "idea_captured": _idea_captured,
    "idea_settled": _idea_settled,
    "domain_creating": "建立「{title}」這個領域",
    "domain_archiving": "封存「{title}」這個領域(資料都留著)",
    "domain_reviewed": "「{title}」整份照草稿:確認 {n} 項",
    "sources_assigned": "{sources} 算「{title}」",
    "request_live_preview": "「{text}」已上線(正在跑的版本包含 {commit})",
    "request_live": "標成已上線:{text}(依 commit {commit} 認定)",
    "ask": "{text}?",
    "yes": "要",
    "no": "不要",
}
