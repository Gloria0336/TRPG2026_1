# AI Living World MVP

這是一個從 [`AI_Living_World_design_v1.0.md`](AI_Living_World_design_v1.0.md) 萃取出來、極度簡化的 MVP。
它保留原設計的**核心原則**：*程式負責所有骰子、狀態與判定；AI 只負責解析意圖與敘事，絕不碰任何數值*，但先捨棄生活世界模擬、資料庫、派系與自訂等級系統，快速驗證三件事：

1. **AI GM 可行性**：AI 能不能在不偷改數學結果的前提下，跑出忠實的 D&D 遊戲？
2. **互動手感**：Discord 互動（A/B/C 方法按鈕、骰子按鈕）和網頁儀表板是否好用？
3. **新手導入**：TRPG 新手能不能零準備直接加入？

### 這個 MVP 包含什麼

- **標準 D&D 5e**：d20 屬性／技能檢定對抗 DC，以及完整的回合制戰鬥流程（先攻、動作經濟、攻擊對 AC、傷害、豁免、死亡豁免）。
- **兩名預製 3 級玩家角色**：一名戰士與一名牧師；另有獨立玩家入口網站提供輕量角色建立。
- **一個短劇本**：*The Dawnbridge Caravan*，約 30-45 分鐘、4 個場景的一次性冒險。
- **Discord bot**（遊玩）**+ localhost 唯讀網頁儀表板**（旁觀）**+ Vercel 玩家入口網站**。
- **沒有資料庫**：使用單一記憶體內 `GameState`，並快照到 `save/session.json`。

---

## 架構（單一程序，無資料庫）

因為沒有資料庫用來共享狀態，Discord bot 與 FastAPI 儀表板會在**同一個 Python 程序、同一個 asyncio loop** 中執行，並共享同一個記憶體內 `GameState` 物件（MVP 中將原設計簡化為單體架構）。

```text
Discord `/action` 自然語言 -> [AI] 意圖解析（便宜模型）-> A/B/C 分級
   A -> engine 執行判定 -> 骰子按鈕 -> 伺服器擲骰 -> 揭示結果 -> [AI] 敘事（強模型）
   B -> 方法按鈕 -> 玩家選擇 -> (A)
   C -> 釐清按鈕 -> 玩家選擇 -> (A)
        -> 每個結果都附加到記憶體內 event_log
   Discord embeds  <->  shared GameState  <->  Web dashboard（SSE 即時更新）
```

| 層級 | 模組 |
|---|---|
| 判定引擎（真相來源） | [`app/engine/`](app/engine)：`dice.py`, `rules_5e.py`, `combat.py`, `resolution.py`, `types.py` |
| AI 協調器（OpenRouter） | [`app/ai/`](app/ai)：`orchestrator.py`, `prompts.py`, `schemas.py` |
| 狀態（記憶體 + JSON 快照） | [`app/state/game_state.py`](app/state/game_state.py) |
| 內容（PC / 怪物 / 劇本） | [`app/content/`](app/content) |
| Discord 前端 | [`app/discord_bot/`](app/discord_bot)：`bot.py`, `views.py`, `embeds.py` |
| 網頁儀表板 | [`app/web/`](app/web) + `static/` |
| 玩家入口網站 | [`portal/`](portal)：Vercel 靜態站，呼叫 FastAPI 的 `/api/portal/*` |
| 進入點 | [`app/run.py`](app/run.py) |

AI **絕不碰數字**：意圖輸出會透過 schema 驗證（DC 提案會對齊到 5e 錨點），敘事也只會戲劇化已經計算完成的 `ResolutionResult`。這件事由 `tests/test_ai.py` 裡的防護測試強制保證。

---

## 設定

### 1. 前置需求

- **Python 3.11+**（開發環境使用 3.14）。
- **Discord bot token**：https://discord.com/developers/applications
- **OpenRouter API key**：https://openrouter.ai/keys （選用；沒有 key 時會以離線 fallback 模式執行，使用預設敘事。）

### 2. 安裝

```powershell
python -m pip install -e .            # 或：python -m pip install -e ".[dev]" 以安裝測試工具
```

### 3. 設定環境變數

複製範例 env 檔並填入內容：

```powershell
Copy-Item .env.example .env
```

編輯 `.env`：

```text
DISCORD_TOKEN=...                     # 執行 bot 必填
DISCORD_GUILD_ID=...                  # 選填：讓 slash command 立即同步到單一伺服器
OPENROUTER_API_KEY=sk-or-...          # 省略時會使用離線 / fallback AI 模式
MODEL_INTENT=openai/gpt-4o-mini       # 便宜模型：意圖解析
MODEL_NARRATE=anthropic/claude-sonnet-4.5  # 強模型：敘事
```

### 4. Discord application 設定

1. 建立一個 application，進入 **Bot**，把 token 複製到 `DISCORD_TOKEN`。
2. 不需要啟用 **MESSAGE CONTENT INTENT**；玩家動作只透過 slash command `/action` 送出。
3. 使用 **`bot`** 與 **`applications.commands`** scopes 邀請 bot，並給予在測試頻道讀取／傳送訊息、使用 embeds／buttons 的權限。

---

## 安裝（第一次下載）

如果是第一次下載或 clone 這個專案，請先進入專案資料夾、建立虛擬環境，並安裝相依套件：

```powershell
cd TRPG2026_1
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

若要執行測試，請改用開發安裝：

```powershell
python -m pip install -e ".[dev]"
```

安裝完成後，確認已依照上方「設定環境變數」建立 `.env`，再執行下一節的啟動指令。

---

## 執行

```powershell
python -m app.run
```

- Discord bot 會連線，儀表板會在 **http://127.0.0.1:8000** 提供服務。
- 如果沒有 `DISCORD_TOKEN`，只會啟動儀表板（適合預覽 UI）。

### 正確關閉與「重複啟動」處理

這個專案一次只能跑一個 `python -m app.run`。Discord bot 和 Web dashboard 共用同一個 in-memory `GameState`，如果開兩個程序，它們會搶同一個 Discord token 和 `8000` port，所以第二個程序會拒絕啟動並顯示「已有另一個實例在執行中」。

正常關閉方式：

```powershell
Ctrl+C
```

請在啟動 `python -m app.run` 的那個 PowerShell 視窗按 `Ctrl+C`。看到類似下面的訊息，就代表正常關閉：

```text
[run] shutting down.
```

如果已經找不到原本啟動的視窗，先看目前鎖檔記錄的 PID：

```powershell
Get-Content logs\app.lock
```

再確認該 PID 是不是這個專案：

```powershell
Get-CimInstance Win32_Process -Filter "ProcessId=<PID>" | Select-Object ProcessId,CommandLine
```

如果 `CommandLine` 裡有 `python -m app.run`，就可以關掉它：

```powershell
Stop-Process -Id <PID>
```

只有在確認沒有任何 `python -m app.run` 還活著，但鎖檔仍然卡住時，才刪除鎖檔：

```powershell
Remove-Item logs\app.lock
```

### 遊玩（在你的 Discord 頻道中）

1. `/start`：開始冒險並顯示兩名英雄。
2. 兩位玩家分別點擊 **Play Bram** / **Play Lyra**，或使用 `/join bram` / `/join lyra`。
3. 兩人都加入後，第一個場景會開啟。使用 `/action` 宣告你要做什麼，例如：`/action 我請 Old Perrin 喝一杯，問他商隊往哪裡去了。`
4. 需要檢定時，點擊**骰子**按鈕擲骰。
5. 常用 slash commands：`/character`, `/scene`, `/roll 1d20+3`, `/next`, `/fight`, `/help`。

可以在瀏覽器中同時開著儀表板與 Discord，觀看角色 HP、先攻追蹤器與冒險紀錄即時更新。

---

## 不透過 Discord 驗證

**離線端到端 smoke test**（使用預設輸入跑完整流程，不需要 Discord，也不需要網路）：

```powershell
python -m scripts.smoke
```

**測試**（確定性的引擎、戰鬥、判定、AI 不碰數字的防護測試，以及儀表板 API）：

```powershell
python -m pytest -q
```

---

## 已知 MVP 簡化

- **單一 session**，綁定到一個頻道；重啟後會從 `save/session.json` 恢復。
- AI context = 最近 N 筆 event log（沒有 RAG / vector memory）；對短篇一次性冒險來說足夠。
- 戰鬥抽象化位置／移動，並省略反應與藉機攻擊；怪物 AI 會隨機選擇一個還活著的目標。
- 「完整 5e 戰鬥」內容只涵蓋兩名預製 PC 與本劇本需要的部分，並非整本 PHB。

## 可調整項目

請見 `.env` 與 `app/config.py`：`DICE_SEED`（確定性骰子）、`NARRATE_CONTEXT_WINDOW`、`AI_OFFLINE`、模型 ID，以及網頁 host／port。DC 錨點位於 `app/engine/rules_5e.py`；劇本與 stat blocks 位於 `app/content/`。

## Vercel 玩家入口網站

GitHub Pages 發佈 workflow 已移除；`app/web/static/` 的 dashboard 保留為 localhost 工具，繼續透過 `python -m app.run` 在 **http://127.0.0.1:8000** 使用。

玩家入口網站是獨立靜態站，位於 [`portal/`](portal)，根目錄的 [`vercel.json`](vercel.json) 會讓 Vercel 只部署這個入口網站。入口網站提供：

- Discord OAuth 登入入口。
- 玩家個人資料與目前綁定角色狀態。
- 任務面板。
- 角色認領與輕量角色創建頁面。

### 後端設定

玩家入口網站仍需要 FastAPI 後端提供 `/api/portal/*`。本機開發時：

```powershell
python -m app.run
```

Discord OAuth 需要在 Discord Developer Portal 的 OAuth2 Redirects 加入後端 callback，例如：

```text
https://your-api.example.com/api/portal/auth/discord/callback
```

後端 `.env` 範例：

```text
DISCORD_OAUTH_CLIENT_ID=...
DISCORD_OAUTH_CLIENT_SECRET=...
DISCORD_OAUTH_REDIRECT_URI=https://your-api.example.com/api/portal/auth/discord/callback
PORTAL_PUBLIC_URL=https://your-project.vercel.app
PORTAL_SESSION_SECRET=請換成長隨機字串
PORTAL_COOKIE_SECURE=1
PORTAL_COOKIE_SAMESITE=none
WEB_CORS_ORIGINS=https://your-project.vercel.app
```

### Vercel 設定

在 Vercel 建立專案並連到這個 repo。部署後，用 `?api=` 指定 FastAPI 後端網址：

```text
https://your-project.vercel.app/?api=https://your-api.example.com
```

入口網站會把 API base URL 記在瀏覽器 localStorage。若要固定寫死，也可編輯 [`portal/config.js`](portal/config.js) 的 `window.TRPG_PORTAL_API_BASE`。
