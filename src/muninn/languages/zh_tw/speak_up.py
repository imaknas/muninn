# what Muninn says when it speaks up in a pause (core/speak_up.py)
TEXTS = {
    "while_away": lambda items, more: f"你不在的時候:{items}" + (f",還有 {more} 則提醒" if more else "") + "。",
    "push": "Muninn 想問你一件事",
}
