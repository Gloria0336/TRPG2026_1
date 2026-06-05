# 標準角色資料格式 — PF2e 版 v1.0

> 配套設計文件：`AI_Living_World_design_v1.0.md` §4.2（混合熟練加值）、§6（資料模型）、§12（階級分軸）。
> 本檔定義 `Character`（PC＋NPC 共用）的**標準資料格式（full PF2e build）**，作為 `app/engine/types.py` 與 `app/content/characters.py` 重構的權威藍圖。
> 數值哲學依 §4.2 **定案的混合制**：5e bounded 熟練加值為基礎曲線，PF2e 五階熟練疊階梯（**非** PF2e 原版 `level + 2/4/6/8`）。

---

## 0. 設計原則

1. **PC／NPC 同表**：以 `is_pc` 區分，欄位結構一致（設計 §6.0）。
2. **三軸分離**：戰力軸＝`level` ＋ PF2e build；制度資格軸＝`guild_rank`（F–S）；廣度軸在 `players`，不在此表。`guild_rank` 不得進入任何熟練/攻擊/AC/DC 加值（§12）。
3. **儲存原值、衍生用算**：屬性值、熟練階級、裝備符文等**原始資料存欄位**；AC、命中、豁免、技能、法術 DC 等**衍生值由程式即時計算**（不存死值），確保改裝備/升級不需手改數字。
4. **熟練一律五階**：未受訓／受訓／專家／大師／傳奇（`untrained/trained/expert/master/legendary`）。所有熟練欄位（含豁免、攻擊類別、防具類別、職業 DC、法術）都用這套，不再用 5e 的「會/不會」布林或能力值豁免清單。
5. **三動作經濟**：動作成本以「動作點數」表達（1／2／3），外加反應（reaction）與自由動作（free）。取代 5e 的 action／bonus／free。

---

## 1. 完整欄位表

### 1.1 身分 Identity

| 欄位 | 型別 | PF2e 意義 | 範例 |
|---|---|---|---|
| `id` | str | 唯一識別 | `"pc_bram"` |
| `name` | str | 角色名 | `"Bram Ironwood"` |
| `is_pc` | bool | PC＝true，NPC/怪物＝false | `true` |
| `level` | int | 角色等級 1–20（戰力軸核心） | `3` |
| `portrait` | str | embed/dashboard 顯示用 emoji | `"盾"` |
| `blurb` | str | 一句登場介紹 | `"穩健的劍盾戰士。"` |

### 1.2 出身 Origin（PF2e build 核心）

| 欄位 | 型別 | PF2e 意義 | 範例 |
|---|---|---|---|
| `ancestry` | str | 種族 | `"human"` |
| `heritage` | str | 血統 | `"versatile_heritage"` |
| `background` | str | 背景 | `"warrior"` |
| `size` | str | 體型 `tiny/small/medium/large/huge` | `"medium"` |
| `traits` | list[str] | 特徵標籤 | `["human","humanoid"]` |
| `languages` | list[str] | 語言 | `["common","dwarven"]` |
| `senses` | list[str] | 感官 | `["low-light"]` |

### 1.3 職業 Class

| 欄位 | 型別 | PF2e 意義 | 範例 |
|---|---|---|---|
| `class_` | str | 職業（`class` 是保留字，欄名加底線） | `"fighter"` |
| `key_ability` | str | 職業關鍵屬性（影響職業 DC、部分攻擊） | `"STR"` |
| `class_hp` | int | 職業每級 HP（用於 HP 衍生） | `10` |
| `class_features` | list[str] | 職業特性／流派/教義 | `["attack_of_opportunity","bravery"]` |
| `subclass` | str \| null | 子職（如牧師教義、術士血脈） | `null` |

### 1.4 屬性 Abilities

| 欄位 | 型別 | PF2e 意義 | 範例 |
|---|---|---|---|
| `abilities` | dict[str,int] | 六屬性最終值（boosts 結算後） | `{"STR":18,"DEX":14,"CON":14,"INT":10,"WIS":12,"CHA":10}` |
| `ability_boosts` | list[str] \| null | （選填）boost 歷程，供重建/驗證 | `null` |

> 能力調整值 `mod = ⌊(屬性值 − 10) ÷ 2⌋`（5e／PF2e 相同）。

### 1.5 熟練 Proficiencies（全部用五階）

| 欄位 | 型別 | PF2e 意義 | 範例 |
|---|---|---|---|
| `perception_prof` | rank | 感知熟練（PF2e 獨立熟練） | `"expert"` |
| `save_prof` | dict[str,rank] | **三豁免**：`fortitude/reflex/will` | `{"fortitude":"expert","reflex":"expert","will":"trained"}` |
| `skill_prof` | dict[skill,rank] | 技能熟練（PF2e 技能清單） | `{"athletics":"expert","intimidation":"trained"}` |
| `lore_prof` | dict[str,rank] | 學識（Lore）熟練 | `{"warfare":"trained"}` |
| `attack_prof` | dict[str,rank] | 武器類別：`unarmed/simple/martial/advanced` | `{"simple":"expert","martial":"expert","unarmed":"expert","advanced":"trained"}` |
| `defense_prof` | dict[str,rank] | 防具類別：`unarmored/light/medium/heavy` | `{"unarmored":"trained","light":"trained","medium":"trained","heavy":"trained"}` |
| `class_dc_prof` | rank | 職業 DC 熟練 | `"trained"` |
| `spell_prof` | rank | 法術攻擊/DC 熟練（無施法者＝`untrained`） | `"untrained"` |

> `rank` ＝ `untrained / trained / expert / master / legendary` 字串。

### 1.6 防禦與生命 Defenses

| 欄位 | 型別 | PF2e 意義 | 範例 |
|---|---|---|---|
| `max_hp` | int | 最大生命（可由衍生算法填，存值供快取） | `44` |
| `hp` | int | 當前生命 | `44` |
| `temp_hp` | int | 臨時生命 | `0` |
| `resistances` | dict[str,int] | 抗性 `{傷害型別: 值}` | `{"fire":5}` |
| `weaknesses` | dict[str,int] | 弱點 | `{"cold_iron":5}` |
| `immunities` | list[str] | 免疫 | `["poison"]` |

> AC 不存死值，由衍生算法計算（見 §2）；若 NPC stat block 直接給定，可用 `ac_override`（int \| null）。

### 1.7 移動 Speed

| 欄位 | 型別 | PF2e 意義 | 範例 |
|---|---|---|---|
| `speed` | km | 地面速度（公里） | `25` |
| `speeds` | dict[str,int] \| null | 其他移動 `fly/swim/climb/burrow` | `null` |
| `movement_base` | float | 引擎移動格基準（沿用既有欄位） | `4.0` |

### 1.8 專長 Feats

| 欄位 | 型別 | PF2e 意義 |
|---|---|---|
| `feats` | list[Feat] | 全部專長，逐項帶類別與取得等級 |

`Feat` 結構：

| 子欄位 | 型別 | 意義 | 範例 |
|---|---|---|---|
| `name` | str | 專長名 | `"Power Attack"` |
| `type` | str | `ancestry/class/skill/general/archetype` | `"class"` |
| `level` | int | 取得等級 | `1` |
| `note` | str | 簡述（選填） | `"一次動作換重擊"` |

### 1.9 法術 Spellcasting（施法者才有，否則 `spellcasting = null`）

| 子欄位 | 型別 | PF2e 意義 | 範例 |
|---|---|---|---|
| `tradition` | str | 傳承 `arcane/divine/occult/primal` | `"divine"` |
| `casting_type` | str | `prepared`（預備）/`spontaneous`（自發） | `"prepared"` |
| `ability` | str | 施法關鍵屬性 | `"WIS"` |
| `cantrips` | list[str] | 戲法（不佔法術位） | `["sacred_flame","light"]` |
| `cantrip_rank` | int | 戲法自動提升到的環階 | `2` |
| `slots` | dict[str,int] | 各環**每日法術位上限** `rank_1..rank_10` | `{"rank_1":3,"rank_2":2}` |
| `slots_used` | dict[str,int] | 各環已用量（當前狀態） | `{"rank_1":0,"rank_2":0}` |
| `repertoire` | dict[str,list[str]] | 自發施法的已知法術（依環階） | — |
| `prepared` | dict[str,list[str]] | 預備施法當日已備法術（依環階） | `{"rank_1":["bless","heal"]}` |
| `focus_points` | int | 專注點（0–3） | `1` |
| `focus_spells` | list[str] | 專注法術 | `["lay_on_hands"]` |

> 法術攻擊/DC 用 `spell_prof` ＋ `spellcasting.ability` 衍生（見 §2）。

### 1.10 裝備 Equipment

| 欄位 | 型別 | 意義 |
|---|---|---|
| `inventory` | list[Item] | 持有物品（取代舊版自由字串清單） |
| `currency` | dict[str,int] | 三幣制 `{"金幣":n,"銀幣":n,"銅幣":n}`（對應 `app/content/currency.py`） |

`Item` 結構（對齊 `app/content/items.py` 既有目錄）：

| 子欄位 | 型別 | 意義 | 範例 |
|---|---|---|---|
| `id` | str \| null | 對應 seed/動態物品 id | `"item_longsword"` |
| `name` | str | 顯示名 | `"長劍"` |
| `category` | str | `weapon/armor/shield/gear/consumable/key_item/treasure` | `"weapon"` |
| `slot` | str \| null | `main_hand/off_hand/armor/trinket/null` | `"main_hand"` |
| `quantity` | int | 數量 | `1` |
| `equipped` | bool | 是否裝備中（影響 AC/攻擊衍生） | `true` |
| `runes` | Runes \| null | 符文（武器/防具才有） | — |

`Runes` 結構（PF2e 符文，提供 item bonus）：

| 子欄位 | 型別 | 意義 | 範例 |
|---|---|---|---|
| `potency` | int | 增幅符文 +1/+2/+3（武器→命中 item bonus；防具→AC item bonus） | `1` |
| `striking` | int | 打擊符文（武器，增加傷害骰數：1=striking,2=greater…） | `0` |
| `property` | list[str] | 屬性符文 | `["flaming"]` |

### 1.11 狀態 Conditions（PF2e 數值化狀態）

| 欄位 | 型別 | 意義 |
|---|---|---|
| `conditions` | list[str] | 狀態 id 清單（含參數式如 `loyal_to:X`） |
| `condition_meta` | dict[str,dict] | 各狀態的值/來源/持續（PF2e 多數狀態帶數值，如 frightened 2） |

### 1.12 死亡軌（PF2e dying，取代 5e 死亡豁免）

| 欄位 | 型別 | PF2e 意義 |
|---|---|---|
| `dying` | int | 瀕死值（達 dying 4 死亡） |
| `wounded` | int | 受傷值（再次瀕死時疊加起點） |
| `doomed` | int | 厄運值（降低致死門檻） |
| `hero_points` | int | 英雄點（PC，可改命/避死） |

### 1.13 世界軸（沿用既有，不變）

| 欄位 | 型別 | 意義 |
|---|---|---|
| `guild_rank` | str | 公會階級 F→S（制度資格軸，§12） |
| `merit` | int | 升階實績累積 |
| `standing` | int | 聲望 |
| `rank_flags` | dict | 階級相關旗標（晉升試煉等） |

### 1.14 載具（沿用既有）

| 欄位 | 型別 | 意義 |
|---|---|---|
| `is_vehicle` | bool | 是否載具 |
| `vehicle_type` | str \| null | 載具類型 |

---

## 2. 衍生值算法（依 §4.2 混合制）

熟練加值函式（核心）：
```
PB(level)            = 2 + ⌊(level − 1) ÷ 4⌋          # 5e 曲線，+2~+6
rank_bonus(rank, L)  = 0           若 rank = untrained
                     = PB(L)       若 trained
                     = PB(L) + 2   若 expert
                     = PB(L) + 4   若 master
                     = PB(L) + 6   若 legendary
mod(score)           = ⌊(score − 10) ÷ 2⌋
```

衍生值：

| 衍生值 | 公式 |
|---|---|
| **AC** | `10 + min(Dex mod, armor_dex_cap) + rank_bonus(穿著防具類別熟練, L) + 防具 potency item bonus + 盾牌/環境` |
| **Perception** | `mod(WIS) + rank_bonus(perception_prof, L) + item/circ` |
| **豁免**（Fort/Ref/Will） | `mod(CON/DEX/WIS) + rank_bonus(save_prof[該豁免], L) + item/circ` |
| **技能** | `mod(屬性) + rank_bonus(skill_prof[技能], L) + item/circ` |
| **學識 Lore** | `mod(INT) + rank_bonus(lore_prof[lore], L)` |
| **武器攻擊（Strike）** | `mod(STR 近戰／DEX 遠程或 finesse) + rank_bonus(武器類別熟練, L) + 武器 potency item bonus + circ − MAP` |
| **武器傷害** | `武器骰 ×(1 + striking) + STR/能力調整 + 屬性/符文/特性加值` |
| **職業 DC** | `10 + mod(key_ability) + rank_bonus(class_dc_prof, L) + item` |
| **法術攻擊** | `mod(spellcasting.ability) + rank_bonus(spell_prof, L) + item` |
| **法術 DC** | `10 + 法術攻擊各分量` |
| **最大 HP** | `ancestry_hp + (class_hp + mod(CON)) × level + 其他加值` |

> 第二次以上攻擊套 MAP（Multiple Attack Penalty）：第二次 −5、第三次 −10（敏捷武器 −4/−8）。
> 同類加值不疊（circumstance／status／item bonus 各取最高），依設計 §4.9。

---

## 3. 完整範例：Bram Ironwood（人類戰士 3 級，PF2e build）

```json
{
  "id": "pc_bram",
  "name": "Bram Ironwood",
  "is_pc": true,
  "level": 3,
  "portrait": "盾",
  "blurb": "穩健的劍盾戰士。拿不定主意時，先揮劍就對了。",

  "ancestry": "human",
  "heritage": "versatile_heritage",
  "background": "warrior",
  "size": "medium",
  "traits": ["human", "humanoid"],
  "languages": ["common"],
  "senses": [],

  "class_": "fighter",
  "key_ability": "STR",
  "class_hp": 10,
  "class_features": ["attack_of_opportunity", "bravery", "fighter_weapon_mastery"],
  "subclass": null,

  "abilities": {"STR": 18, "DEX": 14, "CON": 14, "INT": 10, "WIS": 12, "CHA": 10},

  "perception_prof": "expert",
  "save_prof": {"fortitude": "expert", "reflex": "expert", "will": "trained"},
  "skill_prof": {"athletics": "expert", "intimidation": "trained", "warfare_lore": "trained"},
  "lore_prof": {"warfare": "trained"},
  "attack_prof": {"unarmed": "expert", "simple": "expert", "martial": "expert", "advanced": "trained"},
  "defense_prof": {"unarmored": "trained", "light": "trained", "medium": "trained", "heavy": "trained"},
  "class_dc_prof": "trained",
  "spell_prof": "untrained",

  "max_hp": 44,
  "hp": 44,
  "temp_hp": 0,
  "resistances": {},
  "weaknesses": {},
  "immunities": [],

  "speed": 25,
  "speeds": null,
  "movement_base": 4.0,

  "feats": [
    {"name": "Power Attack", "type": "class", "level": 1, "note": "兩動作換一次加骰重擊"},
    {"name": "Natural Ambition", "type": "ancestry", "level": 1, "note": "額外 1 級職業專長"},
    {"name": "Intimidating Glare", "type": "skill", "level": 2, "note": "可用瞪視威懾"},
    {"name": "Toughness", "type": "general", "level": 3, "note": "增加 HP 與瀕死回復"}
  ],

  "spellcasting": null,

  "inventory": [
    {"id": "item_longsword", "name": "長劍", "category": "weapon", "slot": "main_hand",
     "quantity": 1, "equipped": true,
     "runes": {"potency": 1, "striking": 0, "property": []}},
    {"id": "item_crossbow", "name": "重弩與弩矢", "category": "weapon", "slot": null,
     "quantity": 1, "equipped": false, "runes": null},
    {"id": "item_chain_mail", "name": "鏈甲", "category": "armor", "slot": "armor",
     "quantity": 1, "equipped": true, "runes": {"potency": 0, "striking": 0, "property": []}},
    {"id": "item_shield", "name": "盾牌", "category": "shield", "slot": "off_hand",
     "quantity": 1, "equipped": true, "runes": null}
  ],
  "currency": {"金幣": 0, "銀幣": 12, "銅幣": 0},

  "conditions": [],
  "condition_meta": {},

  "dying": 0,
  "wounded": 0,
  "doomed": 0,
  "hero_points": 1,

  "guild_rank": "F",
  "merit": 0,
  "standing": 0,
  "rank_flags": {},

  "is_vehicle": false,
  "vehicle_type": null
}
```

衍生驗算（level 3，PB = 2）：
- **AC** ＝ 10 + min(Dex 2, 鏈甲 cap 1) + rank_bonus(medium=trained, 3)=2 + potency 0 ＝ **13**（＋盾牌舉起時 +2）。
- **強韌豁免** ＝ mod(CON 14)=2 + rank_bonus(expert,3)=4 ＝ **+6**。
- **意志豁免** ＝ mod(WIS 12)=1 + rank_bonus(trained,3)=2 ＝ **+3**。
- **長劍命中** ＝ mod(STR 18)=4 + rank_bonus(martial=expert,3)=4 + potency 1 ＝ **+9**（第二擊 +4、第三擊 −1）。
- **長劍傷害** ＝ 1d8（striking 0）+ STR 4 ＝ **1d8+4**（揮砍）。
- **運動技能** ＝ mod(STR 18)=4 + rank_bonus(expert,3)=4 ＝ **+8**。

---

## 4. 與現行 `types.py` 的差異（遷移備註）

| 項目 | 現行（5e 殘留） | PF2e 標準格式 |
|---|---|---|
| 豁免 | `save_prof: list[str]`（六能力值豁免） | `save_prof: dict`（三豁免 Fort/Ref/Will 各帶五階） |
| 攻擊 | `Action.to_hit` 寫死平值 | 由 `attack_prof` ＋ 屬性 ＋ 符文衍生 |
| 動作經濟 | `ActionCost`＝action/bonus/free | 三動作：`action_cost` 1/2/3 ＋ reaction/free |
| 死亡 | `death_successes/failures`（5e 死亡豁免） | `dying/wounded/doomed` ＋ `hero_points` |
| 裝備 | `inventory: list[str]` 自由字串 | `inventory: list[Item]` 結構化 ＋ 符文 |
| 組成 | 無 ancestry/heritage/background/class/feats/spells | 全部納入（§1.2–1.9） |
| 防具/武器類別熟練 | 無 | `attack_prof` / `defense_prof` 五階 |
| 職業 DC / 法術熟練 | 無 | `class_dc_prof` / `spell_prof` |

**向後相容**：`Character.from_dict` 須能讀舊快照——`save_prof` 為 list 時轉成 dict（依 5e→PF2e 對應：STR/CON→fortitude、DEX→reflex、INT/WIS/CHA→will）；缺欄位給 PF2e 預設。

### 4.1 實作現況（v1.0 已落地範圍）

本次重構（範圍＝**只改角色資料模型**，不動 combat 引擎）已完成的部分：

- ✅ `save_prof` 改為三豁免 dict、`save_bonus` 改用 PF2e 三豁免衍生；`from_dict` 向後相容舊 list 快照。
- ✅ 新增 build 欄位：ancestry/heritage/background/size/traits/languages/senses、class_/key_ability/class_hp/class_features/subclass、perception_prof、attack_prof、defense_prof、class_dc_prof、spell_prof、resistances/weaknesses/immunities/temp_hp/speeds、feats、spellcasting、dying/wounded/doomed/hero_points。
- ✅ Bram／Lyra 改寫為完整 PF2e build；Lyra 法術改 PF2e（Heal／Divine Lance／Healing Font 等）。

**暫時保留（與本次「不動 combat 引擎」範圍綁定）**：

- `Action.to_hit` ／ `ActionCost`（action/bonus/free）：combat 引擎仍是 5e 動作經濟，故 `Action` 結構與動作 `cost` 維持原樣，`to_hit` 以 PF2e 衍生值「手填」。三動作經濟（§0.5）屬 combat 引擎改造，另案處理。
- `death_successes` ／ `death_failures`：5e 死亡豁免迴圈仍在 `rules_5e.py`，故與新的 `dying/wounded/doomed` 軌**並存**；待 combat 改造時切換為 PF2e dying。
- `inventory: list[str]`：此欄位是 store/inventory 層的**投影快取**（真實來源在 DB/store），多處依賴，故維持字串清單；結構化 Item／runes／currency（§1.10）由 store 層承載，不放進 `Character` dataclass。

---

## 變更紀錄
- **v1.0** — 首版。定義 full PF2e build 標準角色格式，含衍生算法（依設計 §4.2 混合制）、完整範例與 5e→PF2e 遷移對照。
- **v1.0 實作** — `types.py`／`characters.py` 依本格式重構落地（build 欄位、三豁免、PF2e 法術、dying 軌、向後相容）。combat 相關（Action/動作經濟/5e 死亡豁免）依範圍暫留，見 §4.1。341 項測試全綠。
```