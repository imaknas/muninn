# what the assembled Muninn says itself (core/assembly.py)
TEXTS = {
    "no_version": "沒有接上看版本的方式",
    "not_made": "沒做成:{why}",
    "send_packet": "送出投遞包:{title}",
    "scheduled_note": "由 Muninn 排入;在 App 的「我幫你做了」可撤回。",
    "scheduled": "排進行事曆 {hm}:{title}",
    "login_lost": "{label} 的登入失效了",
    "login_lost_detail": "{name}:{why}。修好之前,要用 {label} 的事都不會做,也不會改用本機模型頂替。",
    "talked_since": "後來跟 {called} 談了",
    "closed_unanswered": "{project} 把這個問題結案了,沒有回答。",
    "answered": "{project} 回:{text}",
    "answered_push": "{project} 回了",
    "done": "完成:{title}",
    "over_budget": "今天的模型預算(${limit})用完了",
    "over_budget_detail": "今天呼叫雲端模型花了 ${spent},上限 ${limit}。到明天之前,需要雲端模型的事都先不做:你說的話先記成筆記,"
                          "其他的等明天。要改上限,設 budget.daily_usd。",
}
