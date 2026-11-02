# 安裝與執行 Muninn

[English](../install.md)

在 macOS 或 Linux 上,一行指令就會安裝 Muninn、在背景啟動它(它自己跑排程),
並打開設定頁:選一個模型、貼上它的金鑰,就能在那個瀏覽器開始用。手機要透過 HTTPS 配對:
等 Muninn 有了 HTTPS 位址(見下面),在設定頁掃一個碼。

```bash
curl -fsSL https://raw.githubusercontent.com/imaknas/muninn/main/install.sh | sh
```

或用 [uv](https://docs.astral.sh/uv/):`uv tool install muninn-ai`,再執行
`muninn init` 和 `muninn serve`,打開它印出的設定連結(套件叫 muninn-ai,指令是 `muninn`)。

要升到新版本就執行 `muninn upgrade`:它會裝上最新的穩定版、用新版重啟背景服務並確認它有回應,
沒有回應就換回原本的版本(`muninn upgrade --check` 只說裝的是哪一版、最新的是哪一版)。
有新版時 Muninn 會自己告訴你,也會說是新功能還是只有修正。
`muninn uninstall` 會把 Muninn 從電腦上拿掉:背景服務、手機用的 HTTPS 位址和 Muninn 本身;
資料夾會留著,除非加上 `--delete-data`。

你的手機透過 HTTPS 連到 Muninn:Tailscale 是最快的路:電腦上的 Tailscale 登入了(而且 tailnet 開了 HTTPS 憑證),
設定頁的「配對手機」會自己設好(`tailscale serve`,只有你的 tailnet 連得到),用新位址重啟 Muninn;
缺什麼它會告訴你。手機也要裝 Tailscale App,登入同一個帳號。Muninn 只在電腦開著、醒著的時候運作:
筆電闔上就會暫停。

### Docker(NAS 或伺服器)

在那台機器上 clone 這個 repo,執行 `docker compose up -d`(見 `compose.yaml`);設定頁的連結在
`docker compose logs muninn` 裡。連接埠只開在那台機器自己的位址:從別台電腦要透過 SSH 打開
(`ssh -L 18795:127.0.0.1:18795 <伺服器>`,再開那個連結)。升級:`git pull && docker compose up -d --build`。
要給手機用,就在伺服器上執行 Tailscale(`tailscale serve --bg --https=8444 http://127.0.0.1:18795`),
把它給的位址寫進 `.env` 的 `MUNINN_PUBLIC_URL=https://<名稱>:8444`,再執行一次 `docker compose up -d`;
「配對手機」就會顯示配對碼。伺服器上的 Ollama 在設定頁填 `http://host.docker.internal:11434`。`muninn backup DIR` 會複製資料庫、計畫和設定;`muninn check` 會告訴你設定裡有哪些照寫的樣子沒辦法運作。

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
