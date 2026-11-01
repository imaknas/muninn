# what Muninn's entry point says: settings that cannot work, and what the command line prints (main.py)
TEXTS = {
    "no_git": "這份安裝沒有 git,看不出現在跑的版本",
    "cloud_without_cloud": "llm.routing 是 cloud,但沒有設定雲端模型:在 llm.providers 加一家(或 llm.cloud),或把 routing 改回 privacy",
    "privacy_without_local": "隱私模式(llm.routing: privacy)要有本機模型讀只能留在本機的內容,但沒有設定(llm.providers 裡 location: local 的一家,或 llm.local_model):"
                             "設一個本機模型,或把 llm.routing 改成 cloud(全部交給雲端)",
    "logins_need_serve": "allowed_tailscale_logins 只在 tailscale serve 轉進來時有用;host 是 {host},別的機器可以直接連進來、"
                         "自己填那個 header。把 host 改回 127.0.0.1,讓 tailscale serve 在前面。",
    "handoff_item": " → {effect}「{item}」",
    # 這份安裝沒有的功能(features_off):功能,再來是為什麼、怎麼打開
    "off_understanding": "聽懂你說的話(你說的都先記成筆記,只跑規則提醒)",
    "why_no_model": "還沒設定模型:在 `muninn serve` 印出的設定頁選一個,或寫在 config.yaml 的 llm.providers",
    "off_deep_talks": "跟專案深談(沙盒、權限卡、問專案、把想法升級成 repo)",
    "off_calendar": "行事曆",
    "off_mail": "信件",
    "off_reflection": "每週回顧",
    "off_form_filling": "代填應徵表單",
    "off_git_activity": "專案資料夾裡的進度",
    "off_session_activity": "coding agent session 裡的進度",
    "why_no_runner": "沒有接 session runner(lease_runner_url、lease_runner_token_file)",
    "why_no_google": "沒有設定 Google client(secrets/google_client.json,還有登入回呼用的 public_url)",
    "why_no_mail_accounts": "沒有列信箱(mail_accounts)",
    "why_no_mail_client": "mail_accounts 有列信箱,但它們的 client 沒設定({clients},還有 public_url)",
    "why_reflect_false": "設定關掉了(reflect: false)",
    "why_reflect_privacy": "隱私模式下只有本機模型能讀對話,但沒有設定本機模型",
    "why_no_browser": "沒有瀏覽器可以代填:裝 autofill extra 並執行 playwright install chromium;現在投遞包要自己送",
    "why_no_workspace_roots": "沒有列專案資料夾(workspace_roots)",
    "why_no_transcript_roots": "沒有列 session 紀錄的資料夾(transcript_roots)",
    "budget_not_a_sum": "budget.daily_usd 是 {limit}:要寫大於 0 的美元金額(不設上限就拿掉)",
    "budget_uncounted": "設了 budget.daily_usd,但 {model} 的回覆不會說用了多少 token,花費算不進上限:那些工作換一個會回報的模型"
                        "(API 的供應商),或拿掉上限。在那之前,需要模型的指令都不會跑",
    "budget_unpriced": "設了 budget.daily_usd,但 {model} 沒有價格,花費算不出來:加上 {where},"
                       "寫成 {{input: <美元>, output: <美元>}}(每百萬 token)。在那之前,需要模型的指令都不會跑",
}
