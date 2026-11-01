# 隱私模型

[English](../privacy.md)

模型在哪裡跑是一個設定,`llm.routing`:

- `cloud`:每個請求都送到雲端模型(`claude` CLI,或用你自己金鑰的
  Anthropic API:見下面的 `llm.providers`),工作和未歸屬的資料也一樣,
  而且沒有本機模型會回答,連雲端失敗時也不會(Muninn 會告訴你,並把那句話留成一則筆記):
  本機模型要等 `muninn eval` 證明它做得到,才會接手某個任務。
  這一節其餘的內容在這個設定下說的是哪些東西*留*在本機並加上標記,
  而不是哪些東西不送到雲端。
- `privacy`(設定檔的預設):由下面的規則決定什麼可以離開本機。
  它需要一個本機模型處理必須留在本機的東西,所以你在初次設定畫面設好雲端服務的金鑰時,
  那個畫面會寫入 `cloud` 並當場說明;之後的設定頁也一直會寫清楚什麼送到哪裡。

哪些模型回答是 `llm.providers`:
每個 provider 寫明它的實作(`claude` 是 CLI 和它的登入,
`anthropic` 是 Anthropic API,`openai` 是任何在 `base_url` 提供 OpenAI 式
Chat Completions API 的服務,例如 OpenRouter 或 LM Studio,還有
`ollama`)、金鑰在哪裡(`{secret: <file in the secrets directory>}` 或 `{env: <VARIABLE>}`,絕不是金鑰本身),以及每個層級用哪個模型。
可以有一個 provider 設成 `location: local`(Ollama 的預設;它的 `base_url` 必須
在這台機器或這個網路上):它就是 privacy 路由用來留住敏感內容的本機模型。至於雲端 provider,
`llm.use` 指定哪個 provider 負責 `fast` 層級(你自己說的話以外的一切)
和 `standard` 層級(路由、事實、回答)。金鑰缺了或被拒絕就是登入失效:
Muninn 會告訴你,而且不會有別的模型代替它回答。
沒有 `providers` 的設定照舊使用 CLI。

要限制雲端模型的花費,設 `budget.daily_usd`,並給每個用到的模型價格
(`llm.providers.<名稱>.prices: {<模型>: {input: 3, output: 15}}`,每百萬 token
的美元)。每一次回覆都算,包括 Muninn 檢查自己答案的那些,以及你自己跑的
`muninn eval`(這些超過上限還是會跑完)。一天的呼叫到了上限,到隔天之前都不再問雲端模型:你說的話先記成筆記,
其他工作等明天,Muninn 會告訴你一次。回覆不說用了多少 token 的模型(Claude CLI)
或沒有價格的模型算不進去,`muninn check` 會把它列成問題,而不是讓它超過上限照跑;修好之前,需要模型的指令都不會跑。

不管哪個設定,每個請求和回覆都會存在這台機器上當作評估案例,
連同你的修正(復原的動作、取消的 handoff、對 Muninn 問題的回答、視窗標籤),
所以 `muninn eval --backend <provider:model>` 可以逐個任務告訴你另一個模型(本機的、
更便宜的)能不能接手。還沒有任何案例之前,`muninn eval --suite` 會跑一小組隨 Muninn 附上的虛構案例(一句話在要求什麼、
什麼消息讓事情有了結果、一句話是不是跟工作有關),用在這些任務實際用的模型上,
或用在 `--backend` 上,每種有案例的語言都跑(或用 `--language` 指定一種),
逐個語言告訴你每個模型答對的比例、有多少回覆不符合宣告的格式,以及有多少給你的回覆不是那種語言。
Claude Sonnet 5.5 和 Haiku 5.5 每一題都答對。

- 未歸屬的觀察紀錄,以及歸屬到 `sensitive_domains` 裡列出的領域的任何東西,
  都是**敏感的**。敏感度永遠不會調降。
- `core/llm_router.py` 是通往 LLM 的唯一路徑。敏感或未歸屬的內容
  只會送到本機的 Ollama 模型;它掛了的話,這個請求就跳過,絕不送到雲端。
- 雲端模型只會收到在 `core/privacy.py` 裡用白名單欄位(標題、下一步行動、進度數字)
  建出來的 `CloudSafe` payload,用在晚間摘要的措辭,以及——只有在
  `cloud_drafts: true` 時——根據公開職缺內容和你自己的文件建出來的申請資料包
  (絕不包含計畫筆記、pivot、決策樹或 filters)。標了 `private: true` 的目標或期限不會放進去。
  `tests/test_privacy.py` 和 `tests/test_prepare.py` 證明了這一點。
- 對話摘要(本機模型為每個 session 產生的摘要、主題、
  心情、承諾、決定和摩擦)跟它們來源的文字一樣敏感。它們只有一條路離開
  Muninn:`muninn digests --week YYYY-Www --json`,由你自己執行(或排程),拿去別的地方做你自己的每週反思。
  它印出摘要裡列出的欄位,絕不印對話文字,而且領域在
  `sensitive_domains` 裡或不明的 session(依它的專案今天的歸屬,或依摘要本身判斷)
  一律扣下,只給數量。
- 推播通知會寫出是什麼事(「每週投遞落後:本週 0/4」);payload
  做了端對端加密(RFC 8291),所以暴露的只有鎖定畫面,那由手機的預覽設定決定。
  敏感的項目(工作、未歸屬)只寫 `"<domain>: N new reminders"`;`push_detail: counts`
  會讓每則推播都只寫這樣。
- PWA 要求已配對的裝置(`muninn pair` 給的一次性代碼,之後用
  HttpOnly cookie),還可以再限制成只允許列出的 Tailscale 登入。
