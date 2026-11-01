# asking which goal time away from the keyboard counts toward (core/offline_time.py)
TEXTS = {
    "not_counted": "不算(休息、私事)",
    "duration": lambda minutes: f"{minutes:.0f} 分鐘" if minutes < 90 else f"{minutes / 60:.1f} 小時",
    "focus_said": "剛剛「{name}」{duration}",
    "gap_said": "{start}–{end} 電腦沒動({duration})",
    "focus_ask": "{said},算哪個目標?",
    "gap_ask": "{said},在做什麼?算哪個目標?",
    "logged": "記進「{goal}」:{said}",
}
