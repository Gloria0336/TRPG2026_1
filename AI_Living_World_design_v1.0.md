# AI Living World — 設計活文件

> 基礎：交接書 V1.0（願景層）＋技術決策層持續累加
> **狀態：設計骨架 v1.0 完成（A/B/C 三支柱全定，詳見 §13）**
> 狀態圖例：✅ 已定案　🔶 方向已定、細節待補　❓ 未定
> 本文件為「活文件」：每項決策一旦拍板即收錄，並保留理由與「可調」標記，供整體 review。

---

## 0. 文件說明

本專案目標不是傳統 TRPG 平台，也不是單純 AI 跑團工具，而是介於
**TRPG / MMORPG / Living World / West Marches / 冒險者公會模擬 / AI GM 敘事** 之間的新型態共享世界。

前端：Discord ＋ web dashboard。後端：資料庫 ＋ 程式判定。AI 負責敘事與世界模擬。

---

## 1. 核心願景（源自 V1.0）

玩家並非「參與一場冒險」，而是**共同生活於一個持續運作、會記住歷史、會自我演化的世界**。

- 世界持續存在：即使所有玩家離線，國家運作、NPC 生活、經濟流動、戰爭仍可能爆發。
- 玩家是世界居民，不是世界主角；世界不圍繞玩家運轉。
- 玩家行為造成永久影響：城鎮興衰、勢力崛起或滅亡、商路建立、戰爭結果改變。
- 玩家自由加入／離開，不依賴固定隊伍、不依賴固定 GM。
- 長期目標：玩家多年後回歸，仍能在史書、NPC 口中、城市建築、組織制度中看見自己留下的痕跡。

---

## 2. 最高設計原則 ✅

> **玩家不能成為世界運作的單點故障。**

任何功能都不得出現「玩家離線即停擺 / 缺席即卡劇情 / 請假即世界無法運作」。
因此所有系統都必須具備以下三種接手能力之一：**NPC 代理 ／ AI 代管 ／ 制度接手**。

下列機制皆為服務此原則而存在：AI 代管、NPC 代理人、制度優先、多事件線並行、F–S 冒險者階級、AI 動態內容生成。

---

## 3. 世界與玩家設計（源自 V1.0，含狀態）

| 系統 | 設計 | 狀態 |
|---|---|---|
| Living World | 世界持續存在、可被玩家改變 | ✅ 原則 |
| 玩家定位 | 冒險者，非天選之人／救世主 | ✅ |
| 組隊 | 隊伍可隨時重組，世界持續一致 | ✅ |
| 劇情結構 | 無單一主線，多事件線並行（王位／商會／邊境／遺跡…）；玩家自由參與，結果改變世界 | ✅ |
| 缺席代管 | 玩家離線時 control_mode→ai_managed，依方針＋隨機表行動（走判定脊椎），登入給離線報告 | ✅ 見 §7 |
| 技能缺口 | 不存在唯一解，每個挑戰至少有技能／魔法／暴力／金錢／社交多種解法 | ✅ 原則 |
| 成長：角色等級 | 採 PF2e 1–20 級作為角色戰力與規則複雜度核心；角色由 ancestry／heritage／background／class／feats／spells／equipment 構成 | ✅ 見 §4／§12 |
| 成長：公會階級 | F→E→D→C→B→A→S 改為世界制度承認與資格軸，不取代 PF2e 等級；決定任務／地區／資源／情報權限 | ✅ 見 §12 |
| 成長：來源 | 角色成長遵循 PF2e：等級、能力提升、技能提升、各類專長、法術、裝備與符文；人脈與聲望進世界層，不直接加戰力 | ✅ 見 §12 |
| 玩家資歷／廣度 | 因「玩家=角色」，資歷掛 players（廣度軸），解鎖建公會／發任務／招 NPC／建據點／導師，不加戰力 | ✅ 見 §6 |
| 世界時間 | 雙層時鐘：環境 tick（真實時間、微觀）＋ 章節層（事件驅動、宏觀、無 GM） | ✅ 見 §7 |
| 玩家掌權 | 「制度優先」：玩家接管既有制度高層職位，設政策槽方向，細節由 NPC 代理人執行 | ✅ 見 §11 |
| NPC 代理人 | AI 自動生成顧問／幕僚／官員／副會長（faction_memberships.is_agent），玩家定方向、代理人按政策槽執行 | ✅ 見 §11 |
| 勢力模板 | 建組織時 AI 自動生成行政架構（factions.template）；商會：財務／商路／倉儲主管，學院：教授／助教／館長 | ✅ 見 §11 |
| 內容耗盡 | AI 動態生成：玩家消滅山賊 → 權力真空 → 新勢力崛起 → 新任務 | ✅ 見 §7.5 |

---

## 4. 判定底層 v0.1 ✅（本次定案）

### 4.0 AI ／ 程式分工原則
為確保 living world 長期一致性與防止 AI 亂裁定：

- **程式獨佔**：狀態、數字、骰子、成敗判定、時間推進、事件寫入。
- **AI 負責**：意圖解析（玩家自然語言 → 哪種檢定）＋敘事。**AI 永遠不碰數字。**

### 4.1 核心機制：PF2e d20 ＋四階成敗
擲 d20 ＋ PF2e 修正對 DC，結果使用 PF2e 四階：大成功／成功／失敗／大失敗。所有段位由程式計算，AI 只填敘事。

### 4.2 數值框架：PF2e level-based proficiency
本專案採 PF2e 標準數值哲學：等級差具有明確意義，高等角色在同領域會顯著強於低等角色。不得以自訂 bounded accuracy 取代此結構。

- 角色等級：1–20，為戰力與規則複雜度核心。
- 熟練階級：未受訓／受訓／專家／大師／傳奇。
- 熟練加值：未受訓通常 +0；受訓以上為 `level + 2/4/6/8`。
- 角色構成：ancestry、heritage、background、class、archetype、ability boosts、feats、spells、equipment、runes、conditions。
- F–S 冒險者階級不進熟練加值，只作制度資格與世界承認（見 §12）。

### 4.3 DC 來源：PF2e DC tables
程式使用 PF2e DC 體系，而非固定七級錨點。

- 常見動作：用 PF2e 對應技能行動、simple DC、skill DC。
- 依挑戰等級的事件：用 level-based DC。
- NPC／怪物／陷阱／法術：用 stat block 內建 DC 或衍生 DC。
- 表外創意行動：AI 可提案「任務等級／難度調整／適用技能」，但最終 DC 必須由程式依 PF2e 表格換算，AI 不得自由給數字。

### 4.4 四階成敗切點（相對 DC 的差距）
- **大成功**：總值 ≥ DC + 10。
- **成功**：總值 ≥ DC。
- **失敗**：總值 < DC。
- **大失敗**：總值 ≤ DC - 10。
- **天然 20**：結果升一階。
- **天然 1**：結果降一階。

「部分成功」不作為基礎骰制；需要 fail-forward 時，以 PF2e subsystem、代價結構或敘事後果承接。

### 4.5 DC 歸屬（分工 fork 定案）
**程式查表為主、AI 為輔且受限**：常見動作 × 環境由程式查 PF2e 表格給 DC；玩家做表外創意行動時，AI 只能提案任務等級、難度調整與適用技能，程式再換算 DC。這保留創意行動，同時防話術壓 DC、保一致。

### 4.6 判定 pipeline（後端核心；同時解掉 AI 一致性＋持久化記憶＋史書自動生成）
```
玩家自然語言輸入
 →[AI] 意圖解析 {actor, action, target, approach=技能}
 →[程式] 查 PF2e DC／AI 提案任務等級與難度調整
 →[程式] 算 modifier（能力＋PF2e 熟練＋裝備/符文＋狀態/環境/協力）
 →[程式] 擲 d20 → 對 DC±10 判定四階成敗 → 天然20/1 升降階
 →[程式] result_degree → 後果：失敗／大失敗或 subsystem 需要時，從代價清單選 {類型, 嚴重度}
 →[程式] 套用 state delta（HP／資源／位置／旗標／世界事件）寫入 DB
 →[程式] 產出結構化 ResolutionResult
 →[AI] 將 ResolutionResult 包成敘事（只填字、不改數字）
 →[程式] 寫入事件日誌（供史書／離線報告／RAG 記憶）
```
**重點**：每次檢定都吐出結構化事件 → 世界自然「記得歷史」。此為 C 層技術架構的接口。

### 4.7 代價結構化
每筆代價 = `{ 類型, 嚴重度, 是否寫入持久狀態 }`。
類型枚舉：`時間流逝 / 行蹤暴露 / 資源損耗 / 留下痕跡 / 引來注意 / 關係惡化 / 延遲後果(債務式)`。
嚴重度由 result_degree 與 subsystem 定義決定（失敗＝輕～中，大失敗＝中～重）。AI 只能選類型＋填敘事，不能自創成敗。

### 4.8 判定範例（撬鎖，PF2e）
- 情境：5 級盜賊凜撬當鋪後門。程式依門鎖等級查 PF2e DC；凜的 Thievery 修正由能力、等級熟練、工具與物品加值計算。
- AI 將「我撬鎖」解析為 Thievery 的 Disable/Open 類行動 → 程式擲 d20 並套 DC±10 與天然20/1 → 得出成功程度。
- 若成功，門鎖開啟；若大失敗，程式抽代價類型 `工具損耗` 或觸發陷阱旗標 → AI 敘述後果。
- 多解一致性：撬鎖／撞門／賄賂守衛／knock 法術／話術，皆走同一條檢定脊椎，僅替換技能欄位。

### 4.9 協力與外部加值 ✅
- **加值類型遵循 PF2e**：circumstance／status／item bonus 同類不疊；penalty 亦按類型處理。
- **協力**：優先使用 PF2e Aid 或 Follow the Expert 思路；async 場景可抽象為「符合熟練條件的支援者提供一次 circumstance bonus 或 Victory Point 貢獻」。
- **協力者技能認證**：協助者須在該檢定的 approach 技能達受訓以上，或有對應 lore／feat／職業能力。
- **環境/情境修正**：用難度調整、circumstance bonus/penalty 或 task level 調整，不自創永久加值。
- **工具／裝備加值**：用 PF2e item bonus、rune、consumable 與特定物品規則。
- **消耗資源換加值**：可提供合規 item/status/circumstance bonus、重擲、降低風險或 subsystem 進度，不直接破壞 DC 表。

### 4.10 對抗檢定（PvP／NPC 抗性）✅
- **被動方轉靜態 DC**：採 PF2e DC = 10 + 對應 modifier（如 Perception DC、Will DC、class DC、spell DC），主動方擲、接 §4.4 四階成敗。只擲一顆、波動小、好程式判。
- **async 關鍵**：被動方離線也能判（不要求被偷襲的離線角色即時擲防禦骰）。
- **NPC 抗性**沿用同機制（靜態 DC），與對抗檢定統一。
- 結果寫 event_log，致死接 §6/§10 死亡與復活制度；**PvP 社會規則**（需否同意、限定區域、通緝後果）屬 A 制度層，此處只定判定機制。

---

## 5. 技術架構 — 拓撲 v0.1 ✅（本次定案）

> 路線：**全新設計（greenfield）**。不沿用既有專案骨架；記憶／embedding 層從頭做對（真向量、CJK-aware 切塊）。

### 5.0 部件總覽
- **前端**：Discord Bot（玩）｜Web Dashboard（看與管理：角色卡／地圖／勢力／史書／離線報告／公會據點／admin）
- **核心後端**：Resolution Engine（判定，真相來源）｜AI Orchestrator（意圖解析＋敘事＋NPC/世界模擬＋DC 提案）｜World Simulator/Scheduler（離線世界推進）
- **資料層**：World State DB（結構真相）｜Memory/RAG（敘事記憶）

資料流（一次玩家行動）：
`Discord 輸入 → Orchestrator 解析意圖 → Resolution Engine 判定（讀寫 World State DB）→ 結構化結果 → Orchestrator 敘事 → 寫 event_log（→ Memory/RAG）→ Discord 回敘事 ＋ Dashboard 更新`

### 5.1 部署形態：模組化單體 ＋ 背景 worker
- `core` 進程：API ＋ Resolution Engine ＋ AI Orchestrator ＋ 資料存取（同 codebase，模組邊界清楚）
- `worker` 進程：World Simulator／Scheduler（世界 tick、NPC 代管、離線報告、事件後處理 → 寫 RAG）
- `discord-gateway` 進程：薄層，持 Discord 長連線
- `web`：SPA 打 core API
*替代：真微服務（單人 ops 成本不值）／純單體（tick 與請求互卡）。可調*

### 5.2 內核：事件驅動 ＋ Postgres event_log（outbox 模式）
每次 state 變更寫結構化事件 `{ actor, action, result, deltas, scope, ts }`；worker 消費 → 餵 Memory/RAG、生史書、組離線報告。**此 log 為「世界記得歷史」的單一來源。**
*替代：訊息佇列（Redis/NATS），規模上來再換，起步別上 Kafka。可調*

### 5.3 記憶庫：同一 Postgres ＋ pgvector
結構狀態走 SQL 表、敘事記憶走 pgvector 向量表——同庫、邏輯分離，落實「state 與 prose 分離」，又省掉第二個資料庫的維運。
*替代：獨立向量庫（Qdrant/Weaviate），資料量大或需進階檢索再拆。可調*

### 5.4 行動同步性
Discord 行動 → 即時 ack（「凜正在撬鎖…」）→ Resolution Engine 同步算（快）→ AI 敘事 async（慢）→ 編輯該訊息補敘事。Web 用 SSE 收狀態更新。**判定同步、敘事非同步。**

### 5.5 建議技術棧（順 Python 慣性，可換）
FastAPI（core）｜discord.py（gateway）｜arq 或 APScheduler（worker/排程）｜Postgres ＋ pgvector｜React SPA（dashboard）。

> 接口備註：worker 的 tick cadence 即 A「世界時間」（事件驅動 vs 章節制）的插入點；tech 內核已是事件驅動，但 in-fiction 推進規則留待 A 定，兩者不綁死。

---

## 6. 資料模型 — v0.1 ✅（本次定案）

> 完整 DDL 見獨立檔 `schema_v0.1.sql`（PostgreSQL 15+ / pgvector）。本節僅摘要決策。

### 6.0 關鍵決策
- **玩家 = 角色，一人一角**：`players ⇄ actors` 為 1 對 1（unique FK）。PC 與 NPC 共用 `actors` 表，NPC 無 player 指向。
- **三條成長軸長在同一人身上**：角色戰力軸＝`actors.level` ＋ PF2e build；世界資格軸＝`actors.guild_rank`（F→S）；廣度/資歷軸＝`players.seniority_level` ＋ `unlocks`（解鎖「能做的事」，不加戰力）。
- **死亡＝PF2e 流程＋世界制度後果**：遭遇中使用 PF2e dying/wounded/dead；死亡事件寫 `event_log`（世界記得），復活/保險/債務/聲望損失由世界制度處理。不做時間回溯；原則上不降 PF2e 等級。
- **狀態存法**：核心熱欄位（level/guild_rank/hp/location/control_mode）＋ JSONB/關聯表存 PF2e build（abilities/proficiencies/feats/spells/equipment/conditions/assets/flags），迭代不改 schema。
- **真相模型（CQRS-lite）**：現況表＝「現在」；`event_log` append-only ＝「歷史」。史書／離線報告／RAG 全衍生自 event_log，不做純事件溯源。
- **記憶層**：單一 `memory_chunks`（pgvector）＋ metadata 過濾（actor/faction/location/scope）決定「誰記得什麼」；真 embedding ＋ CJK-aware 切塊。
- **PF2e 規則真相來源**：角色合法性、數值衍生、DC、熟練、專長前置、法術槽與裝備加值由規則資料與程式驗證；AI 只能讀取/摘要，不能改數字。

### 6.1 表結構速覽
| 群組 | 表 | 用途 |
|---|---|---|
| 玩家 | `players` | 帳號、資歷、解鎖權限（廣度軸） |
| 角色/實體 | `actors` | PC＋NPC 統一；level/guild_rank/hp/location/control_mode ＋ 軟欄位 |
| PF2e 角色構成 | `actor_builds`／`actor_proficiencies`／`actor_feats`／`actor_spells` | ancestry、heritage、background、class、archetype、熟練、專長、法術與衍生數值 |
| 世界 | `locations`／`factions`／`faction_memberships` | 地點階層、勢力（含模板）、NPC 代理人職位 |
| 物品 | `items`／`actor_inventory` | PF2e 裝備、runes、consumables、公式與持有狀態 |
| 歷史 | `event_log` | append-only，outbox 來源，§4.6/§5.2 落點 |
| 記憶 | `memory_chunks` | pgvector 敘事記憶，state/prose 分離 |

### 6.2 與既有層的接口
- `event_log.result_degree / cost / deltas` 直接對應 §4.4 四階成敗與 §4.7 代價。
- `event_log.processed_at` ＝ §5.2 outbox 消費標記，worker 處理後生史書／離線報告／RAG。
- `actors.control_mode = ai_managed` ＝ 離線代管開關（§3 缺席代管的落點）。
- `actors.guild_rank` ＝ §12 公會階級；不得作為 PF2e 攻擊、AC、DC、技能或法術加值來源。

---

## 7. 世界模擬循環 — v0.1 ✅（本次定案）

> worker 進程的核心。實作「玩家離線世界仍運作」＋解決「活躍玩家把世界推太快」。
> 同時定案 A 的「世界時間」。

### 7.0 雙層時鐘
- **層一 — 環境 tick**（真實時間，建議每日 cron，*可調*）：微觀模擬，**不推進宏觀**。錯過不懲罰。
- **層二 — 章節層**（事件驅動，達敘事閾值才推進，**無 GM、worker 自動判定**）：宏觀世界，全玩家共享章節。
- 設計效果：休閒玩家回歸只需學「現在第幾章」，不必追連續時間漂移；活躍玩家衝刺＝累積開章條件，不撞死牆；無人上線時宏觀靜止、微觀仍活（世界「安靜但活著」）。
- 代價：需維護兩個迴圈（起步工程量較大）。

### 7.1 環境 tick 執行順序
① 離線（`ai_managed`）角色行動 → ② NPC 日常／關係微調 → ③ 資源回復、地方微經濟 → ④ 累積各章節目標計量 → ⑤ 組裝離線報告。scope 僅 personal/local。

### 7.2 離線角色行動：純隨機表
- 機制：加權隨機表選 PF2e exploration／downtime／subsystem 行動（研究、Earn Income、Craft、Retrain、Gather Information、偵查、社交、巡邏、照料據點…）。
- 權重輸入：actor 的 PF2e 技能/專長/職業能力、公會階級、地點、方針旗標（玩家可設方針偏置權重，未設走預設）。
- 一致性：選定行動後**仍走 Resolution Engine**（§4 判定脊椎），用 PF2e DC 與四階成敗，結果寫 event_log。
- 限制：離線代管預設不代打完整 PF2e encounter；若觸發戰鬥遭遇，建立 encounter 或轉成 world threat/subsystem，等待玩家上線或隊伍組成。
- 代價（已知）：行為較無個性、離線報告偏模板化；換得近乎零 token、最穩、最好實作，且不侵犯玩家在戰術戰鬥中的 agency。

### 7.3 章節開章：目標混合（旗標＋計量）
- 每章預設數條「章節目標」，每條＝旗標 或 計量 或兩者：
  - 旗標型：某 boss 死／某勢力滅（event_log 寫入特定旗標）
  - 計量型：累積特定 scope 事件達 N 筆
  - 範例：「平定北境」＝ 山賊王旗標 ✓ ＋ 北境 local 事件 ≥ 30
- worker 每 tick 檢查 → 全目標達標 → 自動開下一章。

### 7.4 開章動作（宏觀批次重洗）
批次更新宏觀狀態（勢力版圖／戰線／災厄）→ 寫 global scope event → 廣播全玩家 → 生成新章節目標。

### 7.5 動態內容生成：兩層都有
- **微觀（環境 tick 慢長）**：權力真空 → 新勢力萌芽 → 長出 local 小任務，世界平時也活。
- **宏觀（進章批次）**：勢力版圖重洗、區域劇變集中發生，劇變有節奏感。

### 7.6 離線報告組裝
登入時以 `actor_id` 過濾 event_log（自上次登入後），依 scope 分層彙整成報告；純隨機表行動以模板填字（預設不呼叫 AI，*可選旗標*開啟 AI 潤飾摘要）。

---

## 8. AI 編排 — v0.1 ✅（本次定案）

> AI Orchestrator 內部設計。落實 §4.0「AI 永不碰數字」與防話術／防飄移。

### 8.0 不可碰原則（直接定為原則，非選項）
- **全程結構化 I/O**：意圖解析輸出 JSON `{actor, action, target, approach}`；敘事的輸入是 ResolutionResult、輸出純文字。AI 輸出一律 validate，越界 reject／重試。
- **DC 提案受限**：AI 不直接給 DC 數字；只能提案任務等級、難度調整、適用技能或 subsystem 類型，程式再依 PF2e 表格換算。
- **敘事 context ＝ SQL 結構狀態 ＋ RAG 檢索**：敘事前用 metadata（scope/actor 過濾）＋向量檢索 `memory_chunks`，組「此場景誰記得什麼」。

### 8.1 多代理：固定職能角色
- **意圖解析器**：自然語言 → 結構化意圖（含下方三層級分類）。
- **敘事者（GM voice）**：把 ResolutionResult 包成敘事；**NPC 扮演不開獨立 agent**，由敘事者載入不同 persona context（省成本、語氣一致）。
- **裁判**：表外行動時提案任務等級／難度調整／適用技能；DC 數字仍由程式查 PF2e 表格。
- **世界模擬器**：worker 端，宏觀勢力/經濟/事件推進（§7）。

### 8.2 provider：分層路由
重活（敘事）用強模型、輕活（意圖解析/分類/報告潤飾）用便宜模型，走 OpenRouter 之類路由，控成本。*可調*

### 8.3 意圖解析三層級（行動解析層）
意圖解析器先將玩家輸入分級，輸出結構化意圖供後續 pipeline 消費——**自然語言在此被壓成 `{action, target, approach}` 槽位，敘事性宣稱不會變成機械事實**（防 AI 飄移、防玩家話術）：

| 層級 | 條件 | 處理 |
|---|---|---|
| **A 明確行動** | action／target／approach 皆齊 | 直接送 Resolution Engine 執行 |
| **B 明確目標、方法不明** | 目標清楚、approach 缺 | AI 補全：提出候選方法供玩家選（例：「想進去」→ 撬鎖／翻牆／賄賂） |
| **C 意圖不明** | 連目標都不清 | AI 追問：附意圖推測 ＋ 選項，由玩家修正確認 |

防護要點：話術（如「守衛是我朋友會放行」）至多被解析成一個 `社交 approach`，仍須對 DC 擲骰；解析層只抽取機械槽位，不接受玩家對「成敗／DC／既成事實」的敘事性主張。

### 8.4 整合 pipeline（意圖三層接回 §4.6）
```
玩家自然語言
 →[意圖解析器] 分級 A/B/C → 結構化意圖
    A → 直送判定
    B → 回候選方法 → 玩家選 → 結構化意圖
    C → 追問(推測+選項) → 玩家確認 → 結構化意圖
 →[裁判] 表外才提案任務等級／難度調整／適用技能（程式查 PF2e DC）
 →[Resolution Engine] 算 modifier→擲骰→result_degree→代價→state delta→event_log（§4.6）
 →[敘事者] 載入 persona/RAG context，包成敘事（不碰數字）
 →[程式] 敘事寫 memory_chunks，event 標記待 worker 消費
```

---

## 9. 前端分工 — v0.1 ✅（本次定案）

### 9.0 Discord 輸入：混合
- **主**：自然語言在頻道講話 → bot 經 §8.3 意圖解析。
- **輔**：slash 指令做工具性操作，例：`/character`（看角色卡）、`/report`（離線報告）、`/setplan`（設離線方針，偏置 §7.2 隨機表權重）、`/roll`（手動觸發檢定）。

### 9.1 Discord 輸出：embed ＋ 互動元件
結果用 embed；按鈕／選單承載 §8.3 的 B 層候選方法、C 層澄清選項，以及擲骰按鈕（§9.3）。

### 9.2 Dashboard 頁面（讀為主）
角色卡｜世界地圖｜勢力｜**史書**（event_log 衍生）｜離線報告｜公會/據點管理｜admin/GM 工具。

### 9.3 擲骰按鈕（花俏元件）✅
**不可動搖前提**：按鈕只是觸發器、動畫只是化妝。真骰由 Resolution Engine 伺服器算定（§4.0），動畫只「演」向已定數字；按鈕一次性、點完失效，不可重點重骰。防前端竄改點數。

流程：
```
偵測到需擲骰的檢定 → bot 貼 embed ＋「🎲 擲骰」按鈕
 → 玩家點按 → interaction defer
 → 伺服器擲真骰：nat roll → 修正 → total vs DC → result_degree（§4.4）
 → 貼通用「滾動中」GIF（單則訊息，不逐幀編輯）
 → 單次編輯揭曉：結果 embed（nat roll ＋ 修正拆解 ＋ result_degree）
 → 敘事者接續敘事
```
動畫實作（*可調*）：預設 **1 支通用滾動 GIF ＋ 單次編輯揭曉**（僅 1 個資產、避開編輯速率限制）；可升級為**每點數落點 GIF（nat 20／nat 1 特製）**換取極致質感。

---

## 10. 戰鬥與世界威脅 — v0.1 ✅（本次定案）

> B 判定底層的延伸（建在 §4 判定脊椎上）。核心取捨：PF2e 的戰術深度必須保留；非同步自由改由 exploration/downtime/subsystem 與 world threat 承接。

### 10.0 模型：PF2e Encounter ＋ World Threat Subsystem
分兩層：
- **Encounter**：完整 PF2e 同步戰鬥，使用 initiative、三動作經濟、MAP、reaction、距離/地形、條件與怪物 stat block。
- **World Threat**：非同步世界威脅，使用 Victory Point/clock 類 subsystem 表達偵查、研究、破壞補給、爭取盟友、削弱 boss、穩定結界等進度。

設計原則：離線 AI 不代玩家打完整 PF2e encounter；它只能做 downtime/exploration/subsystem 貢獻。真正需要戰術選擇的場面，等待玩家上線或組成同步場次。

### 10.1 World Threat＝一場競賽（接 §7 世界循環）
每個 boss/威脅有兩條 clock：
- **威脅 clock**：向上漲（龍肆虐／山賊壯大）。
- **應對進度**：玩家透過 PF2e 技能、法術、資源、盟友與 downtime 行動削減或推進。
誰先到頂定結局：討平 boss ↔ 威脅升級成更大災厄。不打 → 威脅升級 → 宏觀後果（接環境 tick／開章）。

### 10.2 威脅 clock 推進：兩者
基礎隨環境 tick 慢漲（時間壓力、「拖越久越糟」）＋ 特定玩家行動或失敗加速漲（玩家可影響）。

### 10.3 多人貢獻累加（不同等級可共事）
玩家上線或離線方針可貢獻適合其 level 的任務：低等角色疏散居民、偵查路線、製作消耗品、蒐集情報；高等角色處理高等遭遇、解除高等魔法、壓制主威脅。

每個貢獻使用 PF2e DC/四階成敗，轉成 Victory Points、clock delta、戰場條件、敵方弱點、補給資源或後續 encounter modifier。低等與高等共玩靠任務分層與 subsystem 接合，不靠壓扁 PF2e 數值。

### 10.4 傷害／死亡（接 §6 死亡與復活制度）
Encounter 中照 PF2e 傷害、dying、wounded、dead、healing、condition 規則處理。真死時寫 event_log，世界記得死亡地點、原因、目擊者與後續政治/宗教/社會反應。

復活不做時間回溯，也不預設刪角；由世界制度承接代價：復活服務、教會債務、公會保險、聲望損失、詛咒、資源消耗或隊友任務。原則上不降 PF2e 等級，避免破壞角色 build 與專長/法術/熟練結構。

### 10.5 同步場次：自由約 ＋ 最終決戰門檻
- 平時任何合適等級的隊伍可自由喬時間開 PF2e encounter（突襲、救援、破壞儀式、斬首行動）。
- 「最終討平 boss」的決戰場須應對進度達某階段才解鎖 → boss 之死是社群共同累積的成就（呼應 §7 開章＝集體成就）。
- 結果接合：同步場跑完整 PF2e encounter，結算後把勝負、資源消耗、死亡、獲得情報、clock delta **寫回同一條 world threat ＋ event_log**。

---

## 11. 制度優先（治理） — v0.1 ✅（本次定案）

> A 設計的核心。落實 §3 玩家掌權／NPC 代理人／勢力模板，是「玩家不是單點故障」最直接的試煉。

### 11.0 核心哲學
**權力屬於制度、玩家只是暫居其位。** 玩家不創造制度、只接管職位；離線久了制度自我延續而非鎖死。否決的兩極：完全自由（離線即亡國）、離線無敵（破壞真實性）。

### 11.1 接管路徑：混合
資歷（§6 `players.seniority`／`unlocks`）解鎖「有資格擔任 X 級職位」，但實際就任仍走制度路徑（任命／繼承／政變／選舉，依勢力性質）。
- **接管既有大制度**（國家高層）：走政策槽 ＋ 代理人。
- **自建小組織**（公會，`unlocks` 含 found_guild）：用勢力模板（§11.5）自動生成架構，同樣配代理人。

### 11.2 玩家管什麼：結構化政策槽
玩家設定有限政策槽（外交立場／稅率方向／軍事姿態／發展重點…），代理人按槽位執行。可程式判、防 AI 飄移、離線可運作（呼應 §8 意圖結構化）。
- 槽位沒涵蓋的大事 → 生 **decision event** 推給玩家拍板（接 §7 事件驅動）。
- decision event 設時限；**逾時用保守預設**（維持現狀／最低風險選項）。

### 11.3 制度代理人運作（離線接手）
`faction_memberships.is_agent` 的代理人**不用 §7.2 隨機表**（治理不能隨機），改按政策槽 ＋ 保守預設執行，並累積 decision event 等玩家上線。

### 11.4 分級三段防鎖死（核心）
| 離線時長（*可調*） | 制度反應 | 玩家地位 |
|---|---|---|
| 短期（< 7 天） | 代理人按政策槽運作，decision event 累積待處理 | 正常，上線即復位、無懲罰 |
| 中期（7–30 天） | **攝政制**：最高階代理人升攝政，獲擴大自主權處理積壓事件（保守預設） | 保留，回來復位 |
| 超長期（> 30 天） | **權力衰減**：制度啟動繼任程序（政變／選舉／任命），職位回流制度或其他合格玩家 | 被請下台（退位），保留資歷但失該職位 |

→ 任何層級離線都有制度接手，徹底防鎖死，且符合「權力屬制度」。

### 11.5 勢力模板（接 §6 factions.template）
建組織／接管時 AI 自動生成行政架構與代理人職位（商會：財務／商路／倉儲主管；學院：教授／助教／館長）。玩家不需自行設計行政體系；生成的職位即 `faction_memberships`（`is_agent=true`）。

---

## 12. 階級與升階 — v0.1 ✅（本次定案）

> A 最後一塊。接 §4.2（PF2e level-based proficiency）、§6（actors.level / actors.guild_rank）、§10（死亡與復活制度）。

### 12.0 階級 gate（F→E→D→C→B→A→S）
公會階級是世界制度承認，不取代 PF2e 角色等級。階級決定三種資格：**任務資格**（高階委託需對應階級才能接）、**地區資格**、**資源權限**（高階裝備/設施/情報/代理人）。
- 地區：**預設軟 gate**（能進但風險自負，高 DC／強威脅 clock 輾壓低階者）；少數極危險區用硬 gate 直接擋。
- 階級＝資格/聲望軸，與 PF2e 等級（戰力軸）及廣度軸（`players.seniority`）分離。

建議對應（非硬綁）：
| 公會階級 | 建議 PF2e 等級帶 | 世界意義 |
|---|---:|---|
| F | 1–2 | 新手、地方委託 |
| E | 3–4 | 穩定冒險者 |
| D | 5–7 | 區域級能手 |
| C | 8–10 | 城邦／邊境重要人物 |
| B | 11–13 | 國家級行動者 |
| A | 14–17 | 大陸級英雄 |
| S | 18–20 | 傳說級存在 |

### 12.1 升階：混合（累積 ＋ 晉升試煉）
累積實績/經驗達標 → 解鎖「可挑戰晉升試煉」→ 通過才實際升階。
- 試煉由**公會（NPC 制度）發起認證** → 讓 §11 制度層有了功能（公會＝認證機構），呼應「冒險者公會」設定、防純刷分。
- 試煉走 §4／§10 判定脊椎：可用 PF2e encounter、skill challenge、influence/research/infiltration 類 subsystem 或 downtime 任務。
- PF2e 升級與公會升階可互相參考但不自動綁死；高等低階代表「實力強但未被制度認證」，低等高階原則上只允許在特殊政治/資歷情境，且不提高戰鬥數值。

### 12.2 速度：指數遞增
低階快速通過（新手有成長感）、高階極緩（A→S 極難）。S 階是制度承認的傳說級稀少頂點，非人人可達；其稀有性來自世界實績、風險與認證，而不是額外數值膨脹。

### 12.3 死亡／失敗後的階級後果
死亡不預設降低 PF2e 等級，也不自動降公會階級。嚴重失敗可造成制度後果：聲望受損、任務資格暫停、保險費上升、債務、需完成復權任務。若真的降階，升回比首次快（保留既有實績），仍需重新達標。

### 12.4 技能成長（PF2e skill increases）
技能熟練提升遵循 PF2e level、class、skill increase、feat 與 retraining 規則。公會階級不得封頂技能熟練，也不得提供技能熟練加值。

Living World 可補上的，是「取得資源與敘事資格」：
- 導師、學院、秘傳流派、工坊與儀式地點，可作為 retraining、稀有 feat、公式、spell access 或 lore access 的世界門檻。
- 使用/訓練累積可作為解鎖 retraining 機會、導師好感、特殊任務或 downtime 折扣，不直接繞過 PF2e 等級前置。
- 玩家資歷與公會階級可以讓角色找到更好的老師、材料與情報，但不直接增加攻擊、AC、DC、技能或法術修正。

---

## 13. 設計骨架完成度 ＋ 下一階段

**設計三支柱全部完成 ✅**
- **A 設計決策**：✅ 世界時間（§7）／制度優先（§11）／F–S 升階（§12）全定。
- **B 判定底層**：✅ PF2e 四階成敗與 level-based proficiency（§4）／PF2e Encounter ＋ World Threat subsystem（§10）／協力與加值類型（§4.9）／對抗 DC（§4.10）。
- **C 技術架構**：✅ 拓撲（§5）／資料模型（§6）／世界循環（§7）／AI 編排（§8）／前端分工（§9）。

**下一階段（已非設計決策，屬實作與內容）**
- 實作：依 §5 技術棧搭 core/worker/gateway/web，落 `schema_v0.1.sql`。
- 數值調校：PF2e DC 對照、任務等級、world threat clock、tick cadence、離線時長門檻、升階曲線等需實測校準。
- 世界內容：具體 lore、地點、勢力、章節目標、晉升試煉設計（可接既有 novel_02／Aldir 等設定）。
- 美術資產：擲骰 GIF（§9.3）、dashboard UI。
- 文件補充：政策槽清單、代價/環境修正查表、Discord 指令完整規格。

---

## 變更紀錄
- **v0.1** — 收錄判定底層（現改採 PF2e d20 ＋四階成敗 ＋ level-based proficiency），定案分工原則與 DC 歸屬。
- **v0.2** — 收錄技術架構拓撲（全新設計：模組化單體 ＋ worker、事件驅動 event_log、Postgres ＋ pgvector），更新 Roadmap。
- **v0.3** — 收錄資料模型（玩家=角色 1對1、PC/NPC 同表、PF2e build 與 guild_rank 分離、死亡走世界制度後果、CQRS-lite event_log、pgvector 記憶層）；完整 DDL 見 `schema_v0.1.sql`。
- **v0.4** — 收錄世界模擬循環（雙層時鐘＝環境 tick＋事件驅動章節層），定案 A 的世界時間；離線角色用純隨機表、開章目標混合、動態內容生成兩層都有。更新 §3 狀態與 Roadmap。
- **v0.5** — 收錄 AI 編排（固定職能角色、provider 分層路由、意圖解析三層級 A/B/C 行動解析層防話術防飄移）；整合 pipeline 接回 §4.6。C 僅剩前端分工。
- **v0.6** — 收錄前端分工（Discord 混合輸入＋embed/元件、dashboard 頁面、擲骰按鈕＝伺服器算定＋預渲染 GIF 化妝）。**C 技術架構全部完成**。
- **v0.7** — 收錄戰鬥系統（現改為 PF2e Encounter ＋ World Threat subsystem）：威脅 clock vs 應對進度競賽、推進靠 tick＋玩家行動、死亡接復活/制度後果、同步場自由約＋最終決戰門檻。B 剩協力/環境加值、對抗檢定。
- **v0.8** — 收錄 §4.9 協力與外部加值（PF2e bonus 類型、Aid/Follow the Expert、協力者技能認證）、§4.10 對抗檢定（PF2e 靜態 DC，async 友善）。**B 判定底層全部完成**。
- **v0.9** — 收錄 §11 制度優先（治理）：接管混合路徑、結構化政策槽＋decision event、制度代理人離線接手、分級三段防鎖死、勢力模板。§3 玩家掌權/NPC代理人/勢力模板/內容耗盡四列轉 ✅。A 僅剩 F–S 升階。
- **v1.0** — 收錄 §12 階級與升階（PF2e 等級為戰力軸、公會階級為制度資格軸、混合晉升＝累積＋公會晉升試煉、技能成長遵循 PF2e skill increases）。**A/B/C 三支柱設計全部完成，設計骨架封頂。** 下一階段轉實作與內容。
