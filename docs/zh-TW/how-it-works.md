# Muninn 怎麼運作

[English](../how-it-works.md)

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
  在 `routing: cloud` 下由雲端模型做(見[隱私模型](privacy.md))。
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
- **專案紀錄**(`src/muninn/contracts/records.v2.md`):同樣的往來,用 Principal Handoff 0.5
  的紀錄(設定成 0.4 的專案仍照 0.4 收發),`POST /api/records` 進、`GET /api/records` 出。Muninn 是 principal side:
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
`cloud_mail: true` 時才可能送到雲端模型;工作郵件只留在本機(在 privacy 路由下;見[隱私模型](privacy.md))。

## 其他系統用的介面

其他系統依賴的介面都發佈在 `src/muninn/contracts/` 裡,各自帶版本:
`muninn handoffs --json`、`muninn digests --json`、`muninn tasks --json` 和
`POST /api/messages`(v1)、專案紀錄(`records.v2.md`,Principal Handoff 0.5)、
深談的權限請求(v2)、電腦收集器送來的東西(`activity.v1.md`)和你在哪裡(`location.v1.md`)。
新增欄位不換版本;改名或改變意義就是新版本,跟舊版本並存,直到讀取端都搬過去。
`tests/test_contracts.py` 拿實際輸出對照 schema 檢查。
