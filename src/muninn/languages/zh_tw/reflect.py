# Muninn's weekly reflection (core/reflect.py)
TEXTS = {
    "weekly_title": lambda since, last: f"本週反思 {since.month}/{since.day}–{last.month}/{last.day}",
    "preview": "{title}(預覽)",
    # the reflection's headings, as the model is asked to write them
    "heading_thinking": "這週你在想什麼",
    "heading_promised": "說過要做的",
    "heading_decided": "你做的決定",
    "heading_time": "時間花在哪",
    "heading_state": "狀態",
    "heading_question": "一個問題",
}
