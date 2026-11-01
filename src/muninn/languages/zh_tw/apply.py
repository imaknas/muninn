# filling in a job's application form (core/apply.py)
TEXTS = {
    "gone": "{title} 的職缺在 {site} 上已經找不到了,看起來已經關了。要收掉這份投遞包嗎?",
    "gone_set_aside": "收掉",
    "gone_why": "職缺已經關了",
    "asking": "{title} 的申請表其他都填好了,還差這些要問你:{questions}",
    "still_asking": "記下了「{label}」。還差:{questions}",
    "filled_now": "都填好了。{title} 的申請表看過沒問題就可以送出。",
    "held": "{title} 的申請表沒辦法自己送出:{why}",
    "unsure": "{title} 的申請表按了送出,但網站沒有說收到了,不知道有沒有送成功。先去信箱找確認信,或到網站上看申請紀錄。"
              "送成功了就按「已經送出了」;沒送成功的話,打開申請頁自己送。我不會再自己送一次,免得重複投遞。",
    "unsure_sent": "已經送出了",
    "no_form": "這份投遞包沒有在填的申請表",
    "no_field": "表上沒有這一欄;有的是:{fields}",
    "not_an_option": "「{label}」只能選:{options}",
    "why_captcha": "網站要人通過驗證(CAPTCHA),要你自己在網頁上按送出",
    "why_code": "網站寄了驗證碼到你的信箱,要你自己在網頁上輸入後送出",
    "why_invalid": lambda fields: f"網站不收這幾欄:{fields},跟我說要改成什麼",
    "why_unreachable": "網頁打不開或跟預期的不一樣,要你自己在網頁上填",
    "site": "在 {site} 上填好",
    "unsupported": "這個網站還不能自動填,要你自己在網頁上投",
}
