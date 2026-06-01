# 對齊 AI_Living_World_design §4 + §8.0（小世界範圍）

把判定底層（B 支柱）對齊到 `AI_Living_World_design_v1.0.md`，刻意只搬適合「兩人小世界」的部分；§5–§7、§10–§12 的大世界系統（DB、worker、世界時鐘、async 進度條戰鬥、治理、F→S 階級）一律不動。戰鬥（attack vs AC）維持標準 5e 二元成敗，本輪只重寫**檢定（ability check）**這條路徑。

## 三批變動概覽

### 第一批：§4.4 三段帶判定 + §4.7 結構化代價
- 屬性/技能檢定改吐 `band ∈ {SUCCESS, PARTIAL, FAILURE}`：
  - 達標即 SUCCESS；差 1–4 = PARTIAL（**部分成功＝成功但附代價**）；差 5+ = FAILURE
  - nat 20 升一階、nat 1 降一階（封頂在 SUCCESS / FAILURE）
  - `success` boolean 保留：PARTIAL 仍視為 success=True（呼應 §4.4「部分成功＝成功」），下游 Discord 流程不必改判斷
- 每筆 PARTIAL/FAILURE 自動掛 `Cost { type, severity, persistent, note }`：
  - 七種 `CostType`：time / exposure / resource / trace / attention / relation / debt
  - 選擇順序：場景 `cost_pool` → 技能預設 fallback → TIME 兜底
  - severity 隨 band 提升（PARTIAL→light，FAILURE→moderate，fumble-FAILURE→heavy）
- §4.3 DC 錨點補上 35（Legendary），prompt 與 schema 同步更新
- Discord embed PARTIAL 改用琥珀色，與綠色 SUCCESS / 紅色 FAILURE 分離
- 敘事 prompt 教 AI 處理 PARTIAL（達成目標 + 將代價織進場面，不可翻成失敗；不可改 type/severity）

### 第二批：§4.9 協力與外部加值 + §4.10 對抗檢定
- 協力規則：第 1 位 +2、第 2 位 +1、其餘 +0，總封頂 +3；**協力者必須對該技能 proficient 才算數**（「外行幫不上忙」）
- 外部加值總封頂 ±10（兩級 DC）；資源消耗折算 +5、納入同一個封頂
- `resolve()` 新增 `helpers / env_tier / tool_bonus / resource_spend` 參數，合成後 cap，並把組成寫進 `deltas` 讓玩家看到「+2 是誰給的」
- `opposed_check(actor, defender, approach, defense)`：被動方靜態 DC = 10 + 被動 mod，主動方走同一條三段帶 pipeline（async-friendly，被動方不必擲）
- Discord 端在「另一位 PC 對該技能 proficient」時，roll prompt 多一顆 🤝 協助按鈕：只有隊友能點、點擊切換加入/退出、行動者按 🎲 時 view.helpers 凍結並餵進 `resolve()`

### 第三批：§8.0 敘事守門（程式層）
- 新增 `app/ai/guard.py`，純函式 `find_violations(prose, result)`：
  - 偵測 `DC 15` / 難度等級洩漏、d20 / 1d6 / 2d8 等骰式、`+3 加值/modifier`、`擲骰/擲出 17`
  - 非 SAVE 結果出現「豁免」即違規
  - prose 的 `\d+ 點傷害` / `恢復 \d+ HP` 必須對得上 `result.damage` / `result.healing` / `deltas` 已公布的數字
  - 刻意不碰中文虛詞數字（「兩三隻」「走了幾百步」），避免假陽性
- `orchestrator.narrate()` 接 guard：違規 → 一次重試（user prompt 加上 `GUARD: previous reply violated …` 提示）；重試仍違規 → 降級到 canned 敘事
- 把「AI 不碰數字」這條 §4.0 原則從 prompt 規範升級成**程式檢核**

## 測試

`pytest -q`：**100 passed**

新增 47 個測試：
- `tests/test_bands_costs.py`（14）：margin→band、nat 20/1 shift、success 語意、cost_pool 選擇與 fallback、嚴重度、決定論、snapshot roundtrip、DC 35
- `tests/test_assist_opposed.py`（14）：協力遞減/封頂、非 proficient 歸零、未知技能不 over-grant、external cap、ability_check 看到 external、opposed DC、target/summary、resolve 整合（含 cap、resource）
- `tests/test_guard.py`（19）：clean prose / 中文虛詞 / 各類洩漏偵測 / 傷害-治療數字匹配 / orchestrator 重試流程（mock `_chat`） / reminder 文字 / prompt 守門措辭

`scripts/smoke`：端到端跑完一場完整戰鬥無回歸（戰鬥路徑未改）。

## 變更檔案

判定引擎核心：
- `app/engine/types.py` — 新增 `ResultBand`、`CostType`、`CostSeverity`、`Cost`；`ResolutionResult` 加 `band` + `cost`
- `app/engine/rules_5e.py` — `classify_band`、`assist_bonus`、`cap_external`、`opposed_check`；`ability_check` 改三段帶並接受 `external_bonus`；DC anchor 加 35
- `app/engine/resolution.py` — `pick_cost`、`_compose_external`；`resolve()` 接 helpers/env/tool/resource，自動掛 cost
- `app/engine/dice.py` — 加 `choice()`，把所有非確定性決定（含代價抽取）走同一條 seeded RNG

AI 層：
- `app/ai/guard.py` — 新檔，§8.0 程式守門
- `app/ai/orchestrator.py` — `narrate()` 接 guard + 重試 + canned fallback
- `app/ai/prompts.py` — 敘事 prompt 教處理 PARTIAL/cost；DC 35 進 enum
- `app/ai/schemas.py` — `suggested_dc` 文件更新到 7 錨點

狀態與內容：
- `app/state/game_state.py` — `Scene` 載入/序列化 `cost_pool`
- `app/content/scenario.py` — 四個場景各填合理 cost_pool

Discord 前端：
- `app/discord_bot/views.py` — `RollView` 支援可選 🤝 協助按鈕
- `app/discord_bot/bot.py` — `_begin_check` 偵測可協助的隊友、把 view.helpers 餵進 resolve
- `app/discord_bot/embeds.py` — PARTIAL 用琥珀色
- `app/discord_bot/i18n.py` — `PARTIAL → 部分成功`

測試：
- `tests/test_bands_costs.py`、`tests/test_assist_opposed.py`、`tests/test_guard.py` 全新
- `tests/test_rules.py` — `nearest_anchor(99)` 預期值改為 35（錨點上限提升）

## 沒搬進來的（刻意延後）

| 章節 | 緣由 |
|---|---|
| §4.2 bounded accuracy 改寫（屬性 −1～+4、技能 +0/+2/+4/+6） | 牽涉重寫角色卡與全部數值；2 人短團用 5e 數字運作良好，等多階級共玩時再換 |
| §5 多進程拓撲、§6 Postgres + pgvector、§7 雙層時鐘 + 章節 | 全部是 living world 的世界模擬層，2 人桌跑不到 |
| §10 async 進度條戰鬥、威脅 clock | 2 人同步桌沒有「離線貢獻」需求；本輪保留標準 5e 同步回合制戰鬥（attack vs AC 維持二元成敗） |
| §11 治理 / 政策槽 / 代理人、§12 F→S 階級 | 預製固定等級角色，沒有制度與升階概念 |

## 對齊現況

B 判定底層的小世界子集已對齊：§4.3、§4.4、§4.5、§4.7、§4.9、§4.10、§8.0、§9.3 全 ✅。`§4.0 AI 不碰數字`從 prompt 規範升級成程式檢核——這條是未來開放多人時最不可退讓的底線。
