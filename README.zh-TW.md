# Muninn

[English](README.md) · 繁體中文

全天候運作的個人助理:收集觀察紀錄,依生活領域追蹤承諾,偵測什麼時候偏離了計畫,
再透過自架的 PWA 推送簡短的提醒,附上具體的修正行動。

執行期資料(計畫、觀察紀錄、金鑰、狀態)放在 `data/`,永遠不會 commit。

## 運作方式

```
sources ──► attribution ──► store ──► drift rules ──► composer ──► channel (Web Push)
(git, Claude Code,   (rules, then        (deterministic,  (dedupe, quiet       │
 plans, ingest API)   local-model         no LLM)          hours, priority     ▼
                      suggestions the                      quotas)        PWA inbox
                      user confirms)
```

- **領域(domain)** 就是計畫檔:`data/domains/<id>/plan.yaml`,加上一個追蹤檔
  (plan 的 `tracker` 鍵,預設 `applications.yaml`)。每個領域有一個
  `priority`(1 最高)和一個 `sensitivity_default`。
- **承諾(commitment)** 分兩種:*目標*(`metric`、`target`、`period: day|week`、
  `priority`、`if_behind: [...]`)或*期限*(`due`、`lead_days`、
  `priority`、`done_when: manual` 或 `{targets: [...], status_at_least: applied}`)。
  追蹤檔的狀態順序:`todo < applied < screening < interview < offer`;
  `rejected` 和 `withdrawn` 算已結束。`pivot`、`decision_tree` 和 `filters`
  是給人看的文字,只出現在週報裡。
- **指標(metric)**(`src/muninn/core/metrics.py`):`applications_submitted`(追蹤檔裡的
  日期)、`hours`(標了該目標的活動區段,加上手動修正)、`late_activity`(目標 `cutoff`
  之後的活動分鐘數)、`sleep_hours`(夜間最長的無活動空檔,在有真正的睡眠資料來源之前先這樣算)、
  `steps`(手機透過 ingest API 回報)。每個指標會宣告自己是*至少*還是*至多*,以及是累積型
  (在整個期間內按比例判斷)還是單次讀數。
- **偏離規則**(`core/drift.py`):按比例算落後、期限進入 `lead_days` 範圍內、
  下一步行動逾期、深夜還在活動、某個領域安靜太久。
- **誰可以打擾你**:priority ≤ 3 的領域會推播;更低的會進晚間摘要,除非訊號升級;
  沒有目標的領域只觀察。每個 priority 層級各有每日推播配額。勿擾時段會把推播壓住,
  但證明你還醒著的訊號(過了 cutoff 還在工作)例外。同一個訊號一天只送一次,除非情況變嚴重。
- **報告**:08:30 晨間簡報、22:30 晚間摘要、週報;各自是收件匣裡的一則,
  領域依 priority 排序。
- **語言**:Muninn 說繁體中文或英文(設定的 `language: zh-TW | en`;沒設就跟著機器的語系)。
  設定畫面一開始就問,設定頁可以切換。畫面、Muninn 說的話、它要模型用什麼語言寫,全部跟著它
  (`muninn/languages/`、`ts/words/`;兩種語言的 prompt 都是英文)。
- **先把事情準備好**(`core/prepare.py`):追蹤檔裡快到期、還沒結案的目標,
  Muninn 會根據公開的職缺內容和你的 `documents` 準備一份申請資料包(匹配度、缺口、
  履歷版本、回答草稿、第一次通話要問的問題)。職缺在 Greenhouse 上的(裝了 `autofill` 這個選用套件),
  它還會在背景的瀏覽器裡把申請表填好(`core/apply.py`):模型從資料包、履歷和你在之前表單答過的內容回答每一欄,
  只有你能答的才在對話裡問你,填好的表(每一欄的內容和一張截圖)等你按一次送出。沒有你按那一下就不會送出;
  網站要人工驗證(CAPTCHA)或寄驗證碼到你信箱時,留給你自己處理。其他網站照舊由你自己投,再點「送出了」。
  Muninn 自己做的每件事都列出來,而且可以復原。

### 活動來源(只取 metadata)

- **git**:commit 時間、repo 目錄名稱、依副檔名統計的檔案/行數,
  以及 `Co-Authored-By` trailer 有沒有寫到 AI 助理(有的話算
  `agent_commit`,不算親手做的時間)。不取訊息、路徑或 diff。
- **Claude Code 對話紀錄**:時間戳、工作目錄和 session id;
  只有*有人在場*的活動才算(一則人下的 prompt 加上之後的 10 分鐘),
  所以 agent 自己跑的時間不算你的時間。不取訊息內文。
- **電腦活動**(`src/muninn/contracts/activity.v1.md`):電腦上的收集器送來的東西;
  Mac 收集器(`src/muninn/collectors/mac`,用設定頁上那一行指令安裝)送出非 AFK 時的
  ActivityWatch 視窗活動(跟瀏覽器擴充功能重疊的 Chrome 視窗會帶
  `browser_profile=work`;擴充功能只裝在工作用的 profile)、筆電的 commit metadata,
  以及同步過來給對話紀錄來源用的工作用 Claude 對話紀錄。
- **反思**(`core/reflect.py`):你跟助理的對話文字
  (你的訊息和助理寫出的回覆;絕不包含工具的輸入/輸出或隱藏的推理;只限你有打字的 session)
  每晚逐 session 整理成摘要(summary、commitments、
  decisions、themes、mood、friction)。週一的反思(針對上一個完整的週)在程式裡統計這些,
  再讓模型寫一段簡短的反思。在隱私路由下這兩件都由本機模型做,對話文字絕不會送到雲端模型;
  在 `routing: cloud` 下由雲端模型做(見下面的隱私模型)。
  自己有維護紀錄的專案(`external_todos`、`project_states`、`weekly_stats`)
  是專案待辦和決定的權威來源:反思只管個人的承諾、心情和摩擦,
  專案的決定引用那些紀錄,你的專案待辦直接顯示,不重新抽取。
- **Ingest API**:`POST /api/ingest`,帶 `Authorization: Bearer <data/secrets/ingest_token>`
  和 `{"observations": [{source, key, ts, kind, payload, domain?, goal?, sensitivity?}]}`。
  外部觀察紀錄預設為 `sensitive`。新的觀察紀錄會馬上評估。
- **專案訊息**:`POST /api/messages`(同一個 bearer token),帶
  `{"messages": [{id, project, type: question|progress|done, text, handoff_id?}]}`,
  由維護專案紀錄的系統送來。`id` 由寄件端決定,重試是安全的。
  question 會在面板裡等你回答;你的回答會變成一則交給那個專案的 handoff,
  `reply_to` 設成該問題的 id。progress 和 done 進收件匣;帶 `handoff_id` 的 `done`
  會把那則 handoff 標成完成。
- **專案紀錄**(`src/muninn/contracts/records.v2.md`):同樣的往來,用 Principal Handoff 0.4
  的紀錄,`POST /api/records` 進、`GET /api/records` 出。Muninn 是 principal side:
  待辦跟問題只有你能結案。專案說某件事做完了,本身不算數:會先問你,除非你給了 Muninn
  常設授權,讓它採信專案 session 的話(可撤回);專案狀態檔裡某一行不見了,一律問你。
  專案不再列出某件待辦,那件待辦維持原樣。

### 代你行動

分三級:做了、可以復原(在 Muninn 自己的行事曆排時段、依申請確認信設定狀態、
準備資料包);準備好、點一下就行(填好的申請表,按一下就送出,網站沒說收到也不會再送第二次;回覆草稿存進信箱、依面試/offer/
拒絕信變更狀態);絕不做(寄信、回覆別人的邀請、付款)。帳號用
`muninn connect google|microsoft` 或 PWA 上的「連結」按鈕連接:
web OAuth client,redirect 是 PWA 的 `/oauth/<provider>/callback`,
以一次性的 state 驗證。Google:Gmail 讀取 + 撰寫(草稿)、
Calendar 讀取 + 事件(只寫入 Muninn 行事曆)。Microsoft 365(工作):只有
Mail.Read 和 Calendars.Read;工作的忙碌時段會併入排程。個人郵件只有在
`cloud_mail: true` 時才可能送到雲端模型;工作郵件只留在本機(在 privacy 路由下;見下文)。

## 隱私模型

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

## 目錄結構

```
src/muninn/core/       domain-agnostic logic; depends only on core/ports.py
src/muninn/adapters/   store (SQLite), plans (YAML), sources, LLM backends,
                       Web Push channel, web app + PWA
src/muninn/adapters/web/ts/   the PWA in TypeScript (strict), compiled into web/static/
                       by `npm run build`; the compiled .js is not committed
src/muninn/main.py     composition root and CLI (the only place adapters are chosen)
src/muninn/contracts/  the interfaces other systems read or post, and the formats Muninn reads
                       (schemas, model decision points), shipped inside the package
examples/              config.yaml and attribution.yaml templates for data/
```

儲存層是 `core/ports.py` 裡的一組小 port,每種紀錄一個
(observations、inbox、tasks、handoffs、leases……)。每個模組只拿它用到的那幾個,
所以另一種後端可以一個 port 一個 port 地寫;`Store` 是全部合在一起,
只有 composition root 和 engine 會拿它(`tests/test_architecture.py` 會檢查這點)。

其他系統依賴的介面都發佈在 `src/muninn/contracts/` 裡,各自帶版本:
`muninn handoffs --json`、`muninn digests --json`、`muninn tasks --json` 和
`POST /api/messages`(v1)、專案紀錄(`records.v2.md`,Principal Handoff 0.4)、
深談的權限請求(v2)、電腦收集器送來的東西(`activity.v1.md`)和你在哪裡(`location.v1.md`)。
新增欄位不換版本;改名或改變意義就是新版本,跟舊版本並存,直到讀取端都搬過去。
`tests/test_contracts.py` 拿實際輸出對照 schema 檢查。

## 安裝

在 macOS 或 Linux 上,一行指令就會安裝 Muninn、在背景啟動它(它自己跑排程),
並打開設定頁:選一個模型、貼上它的金鑰、用手機掃 QR code。

```bash
curl -fsSL <address of install.sh> | sh
```

或用 [uv](https://docs.astral.sh/uv/):`uv tool install muninn-ai`,再執行
`muninn init` 和 `muninn serve`,打開它印出的設定連結(套件叫 muninn-ai,指令是 `muninn`)。

在 NAS 或伺服器上用 `docker compose up -d`(見 `compose.yaml`);設定頁的連結
在 `docker compose logs` 裡。不管哪種方式,你的手機都透過 HTTPS 連到 Muninn:
Tailscale(`tailscale serve`)是最快的路。`muninn backup DIR` 會複製資料庫、計畫和設定;`muninn check` 會告訴你設定裡有哪些照寫的樣子沒辦法運作。

## 從 checkout 執行

```bash
uv sync                            # 要自動填申請表加 --extra autofill(再執行 uv run playwright install chromium)
npm ci && npm run build            # the PWA's TypeScript → web/static/*.js (type-checked)
uv run pytest
uv run muninn init                 # VAPID keys + ingest token in data/secrets/
uv run muninn --dry-run tick       # collect + evaluate, print pushes instead of sending
uv run muninn morning | evening | weekly
uv run muninn serve                # PWA + API on 127.0.0.1:18795
uv run muninn pair                 # one-time pairing code for a new device
uv run muninn devices [--revoke ID]
```

`MUNINN_DATA` 指向資料目錄(預設 `./data`);把 `examples/` 裡的檔案複製過去。
每個 CLI 進入點都是 idempotent 的。這樣執行時,設定裡要有 `jobs: default` 才會跑排程
(安裝腳本和容器會寫進去):`serve` 就會自己跑 tick、早晚報告和夜間反思。

## 測試

```bash
uv run pytest -n 4                 # unit and API tests, 4 at a time (fast; what a merge runs)
uv run playwright install webkit chromium   # once per machine, for the browser tests (Chromium fills forms)
uv run pytest -m e2e -n 8          # the PWA in WebKit, as an iPhone and as a laptop, against the real app over HTTPS
```

瀏覽器測試會先編譯 TypeScript(`npm run build`),使用單元測試的假模型、假時鐘和虛構資料,
擋掉 service worker 讓實際出貨的靜態檔案執行,遇到任何頁面或 console 錯誤就失敗。
safe-area inset(iPhone 的瀏海)沒辦法模擬,所以版面檢查只是近似的。

## 部署

`bin/deploy.sh` 會釘住它開始時的那個 commit,編譯 TypeScript,
跑單元測試和瀏覽器測試(型別錯誤或測試失敗會讓它停下來,什麼都沒改)。
接著執行期的 clone(`MUNINN_RUNTIME`,預設
`~/services/muninn`)直接從這個 checkout 拿那個 commit,執行
`uv sync`(含 `autofill` 選用套件和它用的 Chromium)和 `npm ci && npm run build`,重啟 launchd job,再執行
`bin/smoke.py`:跑起來的伺服器在 `/api/smoke` 上檢查自己的依賴
(store、每個在用的雲端模型、本機模型、Google、session runner、填表用的瀏覽器;
不會問任何模型任何問題),而且提供出去的 app 必須跟 repo 裡的一樣。
這些都過了才會 push `main`。重啟後失敗的版本會自己 rollback,
執行期的 `data/deploys.log` 記下每一次部署,以及為什麼被 rollback;
腳本開頭列出了它的結束碼。同一時間只會跑一個部署。伺服器只對 tailnet 開放,
用 `tailscale serve --https=8444`。第一次在手機上設定:
[docs/d1-checklist.md](docs/d1-checklist.md)。
