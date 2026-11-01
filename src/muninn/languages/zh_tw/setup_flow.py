# the setup page, as the server says it (setup_flow.py)
TEXTS = {
    "no_answer": "「{model}」沒有回答一般的對話,換一個模型(它可能是 embedding 之類不能聊天的模型,或這把金鑰用不了它):{why}",
    "no_folder": "這台電腦上沒有「{path}」這個資料夾",
    "google_needs_https": "登入 Google 後會回到 Muninn 的 HTTPS 位址,但 Muninn 還沒有:先配對手機(設定 → 配對手機),它就會有一個",
    "google_not_json": "這不是 Google 給的檔案:在 Google Cloud 下載這個用戶端的 JSON,再選那個檔案",
    "google_desktop": "這個用戶端是「電腦版應用程式」:請改建一個「網頁應用程式」類型的",
    "google_not_client": "這個檔案裡沒有網頁應用程式的用戶端(client_id 和 client_secret):請重新下載用戶端的 JSON",
    "google_redirect": "在 Google Cloud 這個用戶端的「已授權的重新導向 URI」加上這個,儲存後重新下載 JSON:{uri}",
    "no_list": "服務沒有回應,列不出它的模型;確認網址和網路連線,或過一分鐘再試:{why}",
}
