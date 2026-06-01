# AI Living World — 極簡化 MVP 實作計畫

## Context（為什麼做這個）

`AI_Living_World_design_v1.0.md` 是一份野心龐大的「持續運作共享世界」設計（Postgres + pgvector、世界模擬 worker、自訂 F–S 階級、勢力治理、async 進度條戰鬥）。完整實作前，需要先用一個**極簡 MVP** 驗證三件事：

1. **AI GM 可行性** — 設計文件最核心的原則（§4.0）：**程式獨佔骰子/狀態/判定，AI 只做意圖解析 + 敘事，AI 永不碰數字**。這是整個願景成立與否的關鍵。
2. **前端操作性質** — Discord bot 的互動元件（按鈕/選單/擲骰動畫 §9.3）＋ web dashboard 的呈現是否順手。
3. **TRPG 新手快速進入狀況** — 用預製角色 + 意圖解析 B/C 層引導（§8.3）+ 即時角色卡，讓新手零摩擦開玩。

MVP 刻意**抽掉 living-world 的所有持久化/模擬層**，改用標準 D&D 5e、雙人團、單一短劇本、**無資料庫（記憶體狀態 + JSON 快照）**，但保留 Discord bot + web dashboard 雙前端與「程式算數、AI 敘事」的判定脊椎。

> 既有 repo 僅含設計文件一份（greenfield）。本計畫從零搭建。

### 使用者已確認的決策
- **技術棧**：Python 全棧（FastAPI + discord.py + 無建構步驟的 HTML/JS dashboard）
- **LLM**：OpenRouter（分層路由：意圖解析用便宜模型、敘事用強模型，§8.2）
- **規則範圍**：5e 屬性/技能檢定 **＋ 完整 5e 回合制戰鬥**（初值/先攻、動作經濟、攻擊 vs AC、傷害、豁免、死亡豁免）
- **Dashboard**：唯讀觀戰/檢視（§9.2）

---

## 範圍：保留 vs 捨棄（對照設計文件）

| 設計文件章節 | MVP 處理 |
|---|---|
| §4.0 程式/AI 分工（AI 永不碰數字） | **保留（核心）**。Resolution Engine 獨佔所有擲骰與狀態。 |
| §4 判定機制 | 改用**標準 5e**：d20＋修正 vs DC，成功/失敗（nat20/nat1 加敘事風味）。**不**套用自訂的壓低 d20 + margin 三段帶。 |
| §8.3 意圖解析三層 A/B/C | **保留（簡化）**。A 直接判定；B/C 用 Discord 按鈕給候選方法/澄清選項 → 服務新手引導。 |
| §9.1 / §9.3 Discord embed + 擲骰按鈕 | **保留**。伺服器算定真骰、按鈕只觸發、單次編輯揭曉、點完失效。 |
| §5.2 / §6 event_log | **保留為記憶體 append-only list**（+ JSON 快照），非 DB。dashboard 當冒險日誌/史書-lite。 |
| §5 多進程拓撲（core/worker/gateway/web） | **捨棄**。改**單進程**：discord.py + FastAPI 共用同一 asyncio loop 與同一份記憶體 `GameState`。 |
| §6 Postgres / pgvector / RAG 記憶層 | **捨棄**。改記憶體狀態；AI 敘事 context = 結構化狀態 + 最近 N 筆 event_log（滑動視窗，無向量檢索）。 |
| §7 世界模擬 worker / 雙層時鐘 / 章節 | **捨棄**。單一短劇本，scene-based 推進。 |
| §3 缺席代管 / ai_managed / 離線報告 | **捨棄**。同步雙人團一次跑完。 |
| §10 async 進度條戰鬥 / 威脅 clock | **捨棄**。改**標準同步回合制 5e 戰鬥**。 |
| §11 治理 / 政策槽 / 代理人；§12 F–S 階級 | **捨棄**。預製固定等級 5e 角色。 |

> 規則範圍說明：使用者選「完整 5e 戰鬥」。在 MVP 脈絡下＝**忠實實作 5e 戰鬥核心迴圈**（先攻、move/action/bonus action、攻擊擲骰 vs AC、傷害、條件、豁免、死亡豁免），但**內容只涵蓋預製角色與短劇本所需**的技能/法術/怪物，不複製整本 PHB。

---

## 架構（單進程、無 DB）

關鍵決策：**單進程**。因為沒有 DB 可共享狀態，Discord bot 與 web dashboard 必須直接讀同一個記憶體中的 `GameState` 物件 → 兩者跑在同一 Python 程序、同一 asyncio loop。`run.py` 以 `asyncio.gather` 同時啟動 `uvicorn.Server.serve()`（web）與 `bot.start()`（Discord）。

### 資料流（一次玩家行動，源自 §8.4 簡化）
```
Discord 自然語言
 →[AI 意圖解析器/便宜模型] 輸出 JSON {actor, action, target, approach} + 分級 A/B/C
    A → 直送判定
    B → bot 回候選方法按鈕 → 玩家點選 → 結構化意圖
    C → bot 追問(推測+選項) → 玩家確認 → 結構化意圖
 →[Resolution Engine/程式] 需擲骰時：bot 貼 embed + 🎲 按鈕
    → 玩家點 → interaction defer → 伺服器擲真骰(engine) → 算 modifier/DC → 成敗
    → 單次編輯揭曉 embed（nat roll + 修正拆解 + 結果）
 →[AI 敘事者/強模型] 把結構化 ResolutionResult 包成敘事（不碰數字）→ 編輯訊息補敘事
 →[程式] append event_log → dashboard 經 SSE 即時更新
```
戰鬥流程相同，但 Resolution Engine 進入 `combat` 子狀態：先攻排序 → 每個 PC 回合由玩家宣告（NL 或按鈕）→ engine 解算攻擊/法術/移動 → 怪物回合由 engine 內簡單怪物 AI 自動解算 → 敘事者每回合補風味 → dashboard 顯示先攻軌道與 HP/條件。

### 模組（Python，單一 `app/` 套件）
對應設計文件的模組邊界，但全在一個程序內：

- `app/run.py` — bootstrap：載入/快照狀態，asyncio 同時起 FastAPI + discord.py。
- `app/config.py` — `pydantic-settings` 讀 `.env`：`OPENROUTER_API_KEY`、`DISCORD_TOKEN`、`MODEL_INTENT`、`MODEL_NARRATE`、web port。
- `app/state/game_state.py` — 記憶體 `GameState`（characters / scene / scenario flags / event_log / combat / pending_interaction）＋ `save/session.json` 載入與快照。**單一 session**（綁 Discord channel id）。
- `app/engine/dice.py` — **唯一**擲骰處：`d20(advantage|disadvantage)`、傷害骰、可注入 seed 的 RNG（給測試決定論）。
- `app/engine/rules_5e.py` — 標準 5e 數學：能力/技能檢定（d20+mod vs DC）、豁免、攻擊 vs AC、傷害、條件、死亡豁免、先攻。
- `app/engine/combat.py` — 回合制戰鬥：先攻序、回合/輪、動作經濟、簡單怪物 AI、勝負與倒地結算。
- `app/engine/resolution.py` — pipeline 編排：結構化意圖 → 路由到檢定/攻擊/法術 → 產出結構化 `ResolutionResult` → append event_log。**AI 完全不參與此處數字運算**。
- `app/engine/types.py` — `Intent` / `ResolutionResult` / `Event` 等 dataclass。
- `app/ai/orchestrator.py` — OpenRouter client；`parse_intent()`（便宜模型 + JSON schema/function calling）與 `narrate()`（強模型）；輸出一律 validate，越界 reject/重試（§8.0）。
- `app/ai/schemas.py` / `app/ai/prompts.py` — JSON schema（DC 提案受 5e 錨點 enum 約束）、system prompt、GM/NPC persona context。
- `app/content/characters.py` — 2 個預製 PC（建議 martial + caster，例：戰士 + 牧師/法師，等級 2–3，完整 5e 卡）。
- `app/content/monsters.py` — 短劇本所需怪物（例：哥布林群、首領）。
- `app/content/scenario.py` — 短劇本資料（場景、DC、技能挑戰、戰鬥觸發、旗標分支）。
- `app/discord_bot/bot.py` / `views.py` / `embeds.py` — 訊息處理（NL→意圖）、slash 指令（`/start`、`/character`、`/scene`、`/roll`）、embed 渲染、按鈕（B/C 候選 + 🎲 擲骰 §9.3）。
- `app/web/app.py` / `sse.py` / `static/{index.html,app.js,style.css}` — FastAPI 唯讀 dashboard：角色卡、目前場景、event log、戰鬥軌道；SSE 即時推送（無建構步驟，原生 HTML/JS）。
- `tests/` — `test_dice.py`（seed 決定論）、`test_rules.py`（檢定/攻擊/豁免數學）、`test_combat.py`（先攻序、動作經濟）、`test_resolution.py` + **「AI 不碰數字」守門測試**（ResolutionResult 全由 engine 算出、narrator 僅產文字且通過 schema 驗證）。

### 短劇本（新手 onboarding 驗證）
標準 D&D 一次性短團，雙人 ~30–45 分鐘，3–4 場景：
1. **酒館接案** — 社交/技能檢定入門（B/C 層引導新手）。
2. **路途探索** — 技能挑戰（察覺/求生、陷阱或潛行）。
3. **戰鬥遭遇** — 哥布林伏擊，跑完整 5e 戰鬥核心。
4. **高潮抉擇** — 戰/談/潛分支 → 結局寫入 event_log（史書-lite）。

新手輔助：預製角色（無建卡摩擦）、`/start` 玩法說明 embed、B 層方法建議按鈕（「想進去 → 撬鎖/翻牆/說服」）、擲骰按鈕含修正拆解、dashboard 常駐場景 + 角色卡。

---

## 預定 repo 結構
```
TRPG2026/
  AI_Living_World_design_v1.0.md   # 既有
  README.md                        # 安裝/執行/驗證說明
  pyproject.toml                   # 依賴：fastapi, uvicorn, discord.py, httpx, pydantic-settings, sse-starlette, pytest
  .env.example                     # OPENROUTER_API_KEY / DISCORD_TOKEN / MODEL_* / PORT
  app/{run.py, config.py}
  app/state/game_state.py
  app/engine/{dice.py, rules_5e.py, combat.py, resolution.py, types.py}
  app/ai/{orchestrator.py, schemas.py, prompts.py}
  app/content/{characters.py, monsters.py, scenario.py}
  app/discord_bot/{bot.py, views.py, embeds.py}
  app/web/{app.py, sse.py, static/{index.html, app.js, style.css}}
  tests/{test_dice.py, test_rules.py, test_combat.py, test_resolution.py}
  save/  # gitignored，session.json 快照
```

---

## 實作順序（建議）
1. **判定核心（純程式、可離線測）**：`engine/dice.py` → `rules_5e.py` → `types.py` → `resolution.py`，配 `content/characters.py`、`monsters.py`、`scenario.py` 與單元測試。**先把「程式算數」做穩，這是 MVP 的真相來源。**
2. **戰鬥**：`engine/combat.py` + 測試。
3. **狀態與快照**：`state/game_state.py`（記憶體 + JSON）。
4. **AI 編排**：`ai/orchestrator.py`（OpenRouter 分層路由）+ schema 驗證 + 「AI 不碰數字」守門測試。可先用離線 smoke 腳本（餵罐裝意圖跑 pipeline，不經 Discord）。
5. **Discord 前端**：`discord_bot/*`，接 §8.3 三層 + §9.3 擲骰按鈕。
6. **Web dashboard**：`web/*`，SSE 即時更新。
7. **整合 `run.py`**：單進程同跑 bot + web，共用 GameState。
8. **README + `.env.example`**。

---

## 驗證（end-to-end）
- **單元測試**：`pytest`。重點是判定核心決定論（seed 化 dice）、5e 數學正確、戰鬥先攻/動作經濟、以及「ResolutionResult 不含任何 AI 產生的數字」守門測試。
- **離線 smoke 腳本**：不開 Discord，餵幾條罐裝玩家輸入跑完整 pipeline（意圖解析 → 判定 → 敘事），驗證 OpenRouter 串接與 schema 約束。
- **Discord 實測**（Windows 本機）：填 `.env` → `python -m app.run` → 邀 bot 進測試伺服器 → `/start` → 兩名玩家分別行動，驗證：意圖解析 A/B/C 分流、🎲 擲骰按鈕算定與揭曉、完整一場 5e 戰鬥、敘事補字不改數字。
- **Dashboard 實測**：開 `http://localhost:<PORT>`，確認角色卡、目前場景、event log、戰鬥軌道隨 Discord 進行經 SSE 即時更新。
- **驗收標準（對應三個 MVP 目標）**：
  1. AI GM 可行性 — 一場短劇本能從頭跑到結局，且所有數字可追溯為 engine 計算、AI 僅敘事。
  2. 前端操作性 — 擲骰按鈕、B/C 選單、dashboard 即時更新皆運作。
  3. 新手 onboarding — 未讀規則的測試者能靠 `/start` + 按鈕引導獨立完成一場。

---

## 已知取捨與待校準（非阻塞）
- 無 DB＝**重啟只能靠 JSON 快照復原**；單一 session（綁一個 channel）。多桌並行不在 MVP 範圍。
- AI 記憶＝最近 N 筆 event_log 滑動視窗（無 RAG），長劇本會超出視窗 → MVP 用短劇本規避。
- OpenRouter 具體模型 ID 放 config 可換；先用便宜/強各一顆，實測後校準。
- 「完整 5e 戰鬥」內容只覆蓋預製角色 + 短劇本所需，非全 PHB。