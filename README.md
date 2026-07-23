# 宣傳圖卡生成器

宣傳內容請自行核實來源和出處。

## 本機新聞資訊卡工作台

在完成每日話題蒐集後，可在本機建立、審核並輸出 Threads 用的 1080×1080 宣傳圖。工作台支援三種輸入：既有待審話題、HTTPS 網址，以及手動貼上的主題與內容。圖卡只會在人工核准後輸出；完整來源、最終文案、token 用量與費用會保存在 `data/card_records.json`。

### 安裝與啟動

```powershell
python -m pip install -r requirements.txt
$env:OPENAI_API_KEY = "你的 API 金鑰"
python -m topic_monitor.web
```

開啟 `http://127.0.0.1:8765`，即可建立手動草稿。既有待審話題可由 `GET /topics` 讀取；其他 API 入口可供本機介面或自動化流程使用。

第一次啟動會在資料目錄建立 `card_settings.json`。可在其中調整模型、input/output 每百萬 token 單價、輸出 token 上限與每月預算。預估費用僅作決策用途；實際費用以 API 回傳的 usage 記錄為準。

沒有設定 `OPENAI_API_KEY` 時，仍可建立草稿、人工填寫文案、核准與輸出圖卡；系統不會捏造 AI 生成內容。

This local Python program produces up to ten Taiwan public-topic candidates for human selection. It only reads configured public feeds and writes a local JSON review queue. It does not log in to, reply on, or publish to Threads or any other social platform.

## Run once

```powershell
python -m topic_monitor.cli --config config/sources.json --output data
```

The resulting `data/YYYY-MM-DD-review-queue.json` contains source links, score parts, risk flags, and a `pending` review state. Scores indicate public-attention signals only; they are not fact checks or claims about coordinated activity.

## 費用安全的本機測試

建立草稿與 Pillow 圖卡渲染不需要 OpenAI API。AI 功能預設停用；只有在另外完成費用確認並設定 `AI_PROVIDER=openai` 時才可能啟用。

執行完整離線測試：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\run_safe_tests.ps1
```

通過測試不需要公開網際網路連線。

## Sources and review

Edit `config/sources.json` to enable or disable public RSS sources. Keep HTTPS URLs and record only sources whose material you may retrieve. Candidates classified as politics, elections, national security, disasters, health, or finance receive a human-review flag. Do not move a candidate into writing or publication until a person has assessed its evidence and context.

## Daily schedule

In Windows Task Scheduler, create a daily task for 08:00 (Asia/Taipei). Set its program to your Python executable and its arguments to:

```text
-m topic_monitor.cli --config config/sources.json --output data
```

Set the task's “Start in” folder to this project directory.
