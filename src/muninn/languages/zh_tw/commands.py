# what the command runner says itself (core/commands.py)
TEXTS = {
    # in the routing prompt (English for every language): how short a button label and a topic title are
    "label_length": "at most 12 characters",
    "title_length": "at most 10 characters",
    "sent_files": "(傳了 {names})",
    "login_lost": "{who} 的登入失效了,這句先記成筆記;重新登入後再說一次。",
    "no_model": "還沒接上任何模型,這句先記成筆記;到設定頁接上一個模型後,我就聽得懂你說的話。",
    "photos": "照片",
    "pdfs": "PDF",
    "files_local_only": "隱私設定只讓本機模型看{kinds},而本機模型讀不了,這句先記成筆記;換一個讀得了的模型,或用文字說一次。",
    "files_unreadable": "現在設定的模型讀不了{kinds},這句先記成筆記;換一個讀得了的模型,或用文字說一次。",
    "unanswered": "這次沒有模型回應(沒回或連不上),這句先記成筆記;等一下再說一次。",
    "not_understood": "我沒聽懂,已經先記成筆記。可以換個說法再試一次。",
    "not_done": "沒做成:{why}",
    "noted": "記下了。",
    "over_budget": "今天的模型預算用完了,這句先記成筆記;明天(或調高 budget.daily_usd 後)我就聽得懂了。",
}
