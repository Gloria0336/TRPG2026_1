# 舊架構 → 新架構 對照（晨橋商隊抽取案）

> 決定：A 案純沙盒 · 拆檔 · 全域 entities 池 · encounter 掛 location · 刪伏擊 · 捨棄 scenes/goals
> 本文只描述架構，不含任何程式變更。

---

## 1. 檔案佈局

### 舊
```
app/content/
  scenario.py        ← 單檔：TITLE/INTRO/HOW_TO_PLAY + LOCATIONS + SCENES + GOALS + ENDINGS
  characters.py      ← PC 數值塊（Action/Damage/enum）
  monsters.py        ← 怪物模板
  director.py        ← 追 GOALS 進度、決定 nudge / terminal
```

### 新
```
app/content/
  scenario.py        ← 變薄：loader + 驗證 + 對外 API（TITLE/LOCATIONS/...保持同名）
  scenarios/
    dawnbridge/
      meta.yaml      ← title/intro/how_to_play + start_location + default_cost_pool + endings(只剩 defeat)
      locations.yaml ← 世界圖（唯一脊椎）；card 吸收 summary/soft_hooks；+cost_pool +encounter
      entities.yaml  ← 全域實體池，用 location 欄位錨定
  characters.py      ← 不動（數值塊留 Python）
  monsters.py        ← 不動（之後 novel_02 才加新模板）
  director.py        ← ☠️ 變死碼（沒有 GOALS 可追）
```

---

## 2. 職責搬遷表（scenes / goals 拆掉後去哪）

| 舊欄位 | 舊讀取者 | 新家 | 狀態 |
|---|---|---|---|
| `scene.summary` | game_state.set_base_summary | `location.card.base_summary` | 去重併入 |
| `scene.onboarding` | 敘事引導 | `location.card.soft_hooks` | 去重併入 |
| `scene.challenges`（DC） | resolution.determine_dc（**僅 fallback**） | 刪除，AI `DCAssessment` 已是 DC 主來源 | ✅ 丟 |
| `scene.cost_pool` | resolution.pick_cost | `location.cost_pool`（可選）→ `meta.default_cost_pool` | 搬到 location |
| `scene.encounter` | game_state / bot（含寫死 `id=="ambush"`） | `location.encounter` | ⚠️ 需改引擎讀取點 |
| `scene.entities` | game_state.seed_entities | `entities.yaml` + `location` 錨點 | 搬到全域池 |
| `first_scene()` | 新遊戲起點 | `meta.start_location` | 改參照 |
| `GOALS`(done_flags/nudge/terminal) | director.py | — | ☠️ 整組捨棄 |
| `ENDINGS.victory/peaceful` | bot._advance_story（terminal 觸發） | — | ☠️ 無觸發點，移除 |
| `ENDINGS.defeat` | bot._end_game（TPK 觸發） | `meta.endings.defeat` | ✅ 保留（引擎機制） |
| 伏擊 scene "ambush" + `loc_warren_mouth` | — | — | 🗑️ 依指示刪除 |

---

## 3. 誰讀誰（新架構的 .py ↔ .yaml）

```
                       ┌──────────────────────────────┐
                       │  scenario.py (loader + API)   │
                       │  載入時驗證；對外維持同名 API   │
                       └───────────┬──────────────────┘
              讀 meta / locations / entities (一次)
       ┌───────────────┬───────────┴────────────┬──────────────────┐
       ▼               ▼                        ▼                  ▼
  meta.yaml       locations.yaml          entities.yaml      （characters.py
  title/intro     世界圖 + card           全域實體池          monsters.py 不變）
  start_location  cost_pool/encounter     location 錨點
  default_cost    soft_hooks
  endings.defeat

  消費端（對外 API 名稱不變，所以這些檔幾乎不用改 import）：
   ├ embeds.py            → TITLE / INTRO / HOW_TO_PLAY        （來自 meta）
   ├ game_state.py        → LOCATIONS / start_location          （來自 meta+locations）
   │                        seed_entities ← 改讀 entities.yaml(依 location)
   │                        base_summary  ← 改讀 location.card
   ├ portal_api.py        → LOCATIONS（ensure_seed_location_cards）
   ├ location_registration→ LOCATIONS（card 種子）
   ├ resolution.py        → cost_pool ← location；challenges 不再存在
   └ bot.py               → encounter ← 改讀 location；ENDINGS.defeat
                            ☠️ 移除 GOALS / director / scene 轉換相關呼叫
```

---

## 4. 資料流（一回合的生命週期）

```
新遊戲
  loader 讀 3 個 yaml → 驗證 → seed_locations
  party_location = meta.start_location (tavern)
  seed entities where location==tavern   (老佩林 / 兜帽客)
  base_summary ← tavern.card.base_summary

玩家輸入自然語言
  │
  ├─ 移動意圖 → travel_path 在 locations 圖上 pathfind（connects/parent，累積 travel_cost）
  │             抵達新 location → seed 該地 entities + 換 base_summary
  │             ❌ 無 next_scene / 無 director / 無 goal nudge
  │
  ├─ 一般行動 → AI DCAssessment 給 DC（無 challenges）
  │             檢定失敗 → pick_cost：location.cost_pool → meta.default_cost_pool → TIME
  │
  ├─ 接任務   → ✅ 走 quest 資料表（System 2，見 §5），照常被記、被讀
  │             （順手寫的 accepted_quest flag = 死寫入，沒人讀）
  │
  └─ 攻擊/開戰 → 引擎讀「目前 location.encounter」生成戰鬥單位
                warren = goblin_boss×1 + goblin×1；❌ 無進場自動伏擊

結束
  ❌ 無 terminal goal → 世界永不自動結束
  ✅ 全隊陣亡 → 引擎 TPK → meta.endings.defeat
  收尾由 GM 手動指令
```

---

## 5. 「任務」其實是兩套系統（別混淆）

```
System 1：accepted_quest flag                System 2：quests 資料表（真任務系統）
  設：bot._sync_story_flags...                 AI GM 敘事中丟 quest seed
  讀：只有 director.py (goals.done_flags)        → store.upsert_quest_seed() 寫表
  ────────────────────────────                  → 你接受 → store.accept_quest()
  A 案下：reader 消失                            → quest agent 展開細節
  flag 仍被寫，但「寫了沒人讀」= 死寫入            → _maybe_apply_quest_check() 讀表判定完成
  ☠️ 可從 bot.py 清掉（純清理）                  ────────────────────────────
                                               A 案下：原封不動、照常運作、照常被讀
                                               ✅ 這才是你「接任務被記住」的地方
                                               👍 本來就是 AI 動態生成，比 goals 更貼合去脈絡化
```

**結論**：捨棄 goals 不會失去任務追蹤。死的是「寫死的進度軌（goals）+ 餵它的 flag」，活的是「動態任務板（quests 表）」。

---

## 6. 活 / 死 一覽

| 元件 | 狀態 | 說明 |
|---|---|---|
| locations.yaml（世界圖） | 🟢 活 · 升格為唯一脊椎 | 移動、敘事、戰鬥、成本全靠它 |
| entities.yaml（全域池） | 🟢 活 | location 錨定，抵達即 seed |
| quests 資料表（System 2） | 🟢 活 · 不受影響 | AI 動態任務，獨立於 goals |
| AI DCAssessment | 🟢 活 · 升格為 DC 唯一來源 | challenges 退場後它本來就主導 |
| meta.endings.defeat | 🟢 活 | TPK 觸發 |
| scenario.py | 🟡 改寫 | 從資料容器變 loader+API |
| game_state / bot / resolution | 🟡 改讀取點 | encounter/cost_pool/entities 改從 location 讀 |
| SCENES（場景軌） | ☠️ 死 | 職責拆給 location |
| GOALS + director.py | ☠️ 死 | 去脈絡化的直接代價 |
| ENDINGS.victory/peaceful | ☠️ 死 | 無 terminal 觸發點 |
| accepted_quest flag | ☠️ 死寫入 | 仍被寫、沒人讀，可清 |
| 伏擊 ambush + loc_warren_mouth | 🗑️ 刪 | 依指示移除 |

---

## 7. 唯一非做不可的引擎改動

其餘多半是「改讀取來源」與「刪死碼」，風險低。真正要動腦的只有一處：

> **encounter 觸發點**：目前 `bot.py:1362` / `game_state.py:463` 把 encounter 綁在 scene，
> 甚至硬寫 `gs.scene.id == "ambush"`。新架構要改成「讀目前 location.encounter」。
> 刪掉伏擊後，剩下的 warren boss 戰不是進場自動觸發，而是「這裡若開戰用這份名單」，
> 與 bot.py:682「玩家攻擊且該地有 encounter」的既有判斷接得起來。
```
