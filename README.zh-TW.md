<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/imaknas/muninn/main/assets/logo/banner-zh-dark.svg">
  <img alt="Muninn:全天候的個人助理,一切都在同一段對話裡" src="https://raw.githubusercontent.com/imaknas/muninn/main/assets/logo/banner-zh.svg" width="100%">
</picture>

<p align="center">
  <a href="https://github.com/imaknas/muninn/actions/workflows/ci.yml"><img src="https://github.com/imaknas/muninn/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/muninn-ai/"><img src="https://img.shields.io/pypi/v/muninn-ai" alt="PyPI"></a>
  <a href="https://github.com/imaknas/muninn/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue" alt="License: Apache-2.0"></a>
</p>

<p align="center"><a href="https://github.com/imaknas/muninn/blob/main/README.md">English</a> · 繁體中文</p>

Muninn 是跑在你自己電腦上的個人助理,你在手機或瀏覽器上用同一段對話跟它說話。它記得你在往哪些目標前進,
發現你偏離計畫時提醒你,而且只講要緊的事,附上下一步該做什麼。它自己做的事都能復原;收不回來的事,一定等你說好。

## 安裝

macOS 或 Linux,這一行會安裝 Muninn、在背景啟動它,並打開設定頁:

```bash
curl -fsSL https://raw.githubusercontent.com/imaknas/muninn/main/install.sh | sh
```

或用 [uv](https://docs.astral.sh/uv/):

```bash
uv tool install muninn-ai
muninn init && muninn serve
```

NAS 或伺服器:`docker compose up -d`(見 [compose.yaml](https://github.com/imaknas/muninn/blob/main/compose.yaml))。更多在 [docs/zh-TW/install.md](https://github.com/imaknas/muninn/blob/main/docs/zh-TW/install.md)。

## 開始使用

1. 在設定頁選一個模型、貼上它的金鑰:Claude、任何有 OpenAI 式 API 的服務,或用 Ollama 跑的本機模型。
2. 直接在那個瀏覽器裡開始跟它說話。
3. 要用手機:打開設定 → 配對手機。電腦上有 Tailscale 的話,Muninn 會在你的 tailnet 上給自己一個 HTTPS 位址;掃碼即可。
4. 其他也都在設定頁:要讀的資料夾、連結 Google(郵件和行事曆,一步一步帶你做)、加上填表功能。

`muninn upgrade` 升到最新版,`muninn check` 告訴你設定裡哪裡照寫的跑不起來,`muninn backup DIR` 備份你的資料。

## 它會做什麼

- **一段對話。** 跟它說發生了什麼、問它事情、回答它的問題。沒有表單要填。
- **依生活領域的目標與期限。** 從它觀察到的事算進度,落後時提醒你,有每日上限和安靜時段。
- **早上的簡報、晚上的總結、每週的回顧。**
- **郵件與行事曆。** Google:讀信、存回覆草稿、在它自己的行事曆上排時間。Microsoft 365:只讀。它從不替你寄信或回覆邀請。
- **求職申請。** 依職缺和你的文件準備每一份申請;裝了 `autofill` extra 的話,還會幫你填好 Greenhouse 表單,你按一下才送出。
- **你的程式專案。** 專案可以把只有你能決定的待辦和問題交給它([Principal Handoff](https://github.com/imaknas/muninn/blob/main/src/muninn/contracts/records.v2.md)),
  有 session runner(例如 [agent-hub](https://github.com/imaknas/agent-hub))的話,一段對話也可以借給專案自己的 session。
- **繁體中文或英文**,從頭到尾。

## 隱私

Muninn 跑在你的機器上,它知道的東西都留在它的 `data/` 資料夾。模型在哪裡跑由一個設定決定:`cloud` 把每個請求送到你選的雲端模型;
`privacy` 把工作和還沒歸類的內容留在本機模型,只給雲端白名單裡的欄位。你可以設定雲端模型每天的花費上限,每台裝置都要配對。
細節在 [docs/zh-TW/privacy.md](https://github.com/imaknas/muninn/blob/main/docs/zh-TW/privacy.md);資安漏洞請照 [SECURITY.md](https://github.com/imaknas/muninn/blob/main/SECURITY.md) 回報。

## 文件

| | |
|---|---|
| 安裝、升級、Docker、手機用的 HTTPS | [docs/zh-TW/install.md](https://github.com/imaknas/muninn/blob/main/docs/zh-TW/install.md) |
| 領域、目標、它觀察什麼、替你做什麼 | [docs/zh-TW/how-it-works.md](https://github.com/imaknas/muninn/blob/main/docs/zh-TW/how-it-works.md) |
| 模型在哪裡跑、什麼會離開這台機器 | [docs/zh-TW/privacy.md](https://github.com/imaknas/muninn/blob/main/docs/zh-TW/privacy.md) |
| 給其他系統的介面 | [src/muninn/contracts](https://github.com/imaknas/muninn/tree/main/src/muninn/contracts) |
| 開發、測試、發佈(英文) | [CONTRIBUTING.md](https://github.com/imaknas/muninn/blob/main/CONTRIBUTING.md) |
| 每個版本改了什麼(英文) | [CHANGELOG.md](https://github.com/imaknas/muninn/blob/main/CHANGELOG.md) |

## 授權

[Apache-2.0](https://github.com/imaknas/muninn/blob/main/LICENSE)
