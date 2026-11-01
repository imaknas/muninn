# what the settings page names each kind of work a model is picked for (core/task_models.py)
TEXTS = {
    "label": lambda group: {"route": "路由", "facts": "事實分析", "answer": "回答", "draft": "草稿", "mail": "信件分類",
                            "reflect": "回顧"}[group],
    "detail": lambda group: {"route": "你說的話要做什麼", "facts": "你說的消息影響哪些事", "answer": "回答你問的問題",
                             "draft": "投遞包(含準備前的對口檢查)", "mail": "新信的分類與回信草稿",
                             "reflect": "對話摘要與每週回顧"}[group],
}
