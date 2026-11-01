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
用 Docker 的話,改成拉新的 image。

在 NAS 或伺服器上用 `docker compose up -d`(見 `compose.yaml`);設定頁的連結
在 `docker compose logs` 裡。不管哪種方式,你的手機都透過 HTTPS 連到 Muninn:
Tailscale 是最快的路:電腦上的 Tailscale 登入了(而且 tailnet 開了 HTTPS 憑證),
設定頁的「配對手機」會自己設好(`tailscale serve`,只有你的 tailnet 連得到),用新位址重啟 Muninn;
缺什麼它會告訴你。`muninn backup DIR` 會複製資料庫、計畫和設定;`muninn check` 會告訴你設定裡有哪些照寫的樣子沒辦法運作。

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
