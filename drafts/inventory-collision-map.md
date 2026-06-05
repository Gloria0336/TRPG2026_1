# 物品模組 × scenario 重構：避雷地圖

> 目的：之後做 scenario（locations/entities/去 goals）重構時，不要誤刪已落地的物品模組。
> 物品模組狀態：✅ 完整落地（11 檔 + items / actor_inventory 兩表）。

---

## 🔴 高風險：兩套重構改到「同一個函式」

都在 `app/state/game_state.py`，這是唯一真正重疊的檔案。

### `new_game()`（約 849–870）
scenario 重構會在這裡改 `seed_locations(LOCATIONS)`、`goto_scene(first_scene())`、entity 種子。
但這函式裡夾著**必須保留**的物品碼：
```python
store.seed_items(item_catalog.SEED_ITEMS)      # L858 ← 別刪
...
_refresh_inventory_projections(gs.characters)  # L864 ← 別刪（PC 起始物品遷移靠這個）
```
✅ 規則：改 location/scene 那幾行，但 `seed_items` 與 `_refresh_inventory_projections` 原樣留著。
順序也別動（seed_items 在 seed_locations 之後；投影在載入 PC 之後）。

### `from_dict()`（809–830）
scenario 重構會碰 `party_location_id` / `scene` 還原。函式尾端有：
```python
_refresh_inventory_projections(gs.characters)  # L829 ← 別刪（載入時重建投影，防快照漂移）
```
✅ 規則：動 scene/location 還原，但保留 L829。

### import（L13）
```python
from ..content import items as item_catalog   # ← 別動
```
就算 scenario 重構把 `from ..content import monsters, scenario`（L14）改寫，這行要留。

### helper（101–103）
`_refresh_inventory_projections` / `_migrate_character_inventory` 是物品模組的，別當成 scenario 死碼刪掉。

---

## 🟡 中風險：同檔案、不同函式（別誤傷）

| 檔案 | 物品碼位置（勿動） | 我的 scenario 改動在 |
|---|---|---|
| `bot.py` | `_apply_entity_updates` 後套用 `item_grants`（約 317） | encounter/ending 在 1257–1395，區段不同 |
| `store.py` | `seed_items` / `grant_item` / `register_item` / `get_inventory` / `find_item_by_ref` | 我改 `seed_entities`（entity 改 location 錨定），它與 `seed_items` 相鄰，別連坐 |
| `embeds.py` | `character_embed` 讀 `get_inventory` | 我只動 start/TITLE/INTRO 那個 embed |
| `resolution.py` / `prompts.py` | `prompts.py:390` implausible 閘門讀 inventory 投影 | 我改 `pick_cost` 的 cost_pool 來源，不同處 |

✅ 規則：這些是「同檔不同函式」，只要編輯時鎖定目標函式、別整段重寫檔案，就不會誤傷。

---

## 🟢 無重疊：scenario 重構完全不碰

`app/content/items.py`、`tests/test_inventory.py`、`schema.sql` 的 items/actor_inventory 表、
`schemas.py` 的 `ItemGrant`、`tests/test_implausible_gate.py` — 這些放心，不在 scenario 重構半徑內。

---

## ⚠️ 兩個非顯而易見的耦合（重構時要記住）

1. **Entity id 現在也是 inventory 的 actor key。**
   `actor_inventory.actor_id` 直接用 `ent_perrin` / `ent_grix` 這類 entity id（PC 與 NPC 同表）。
   我的 `entities.yaml` 把 entity 改成 location 錨定時，**entity id 必須維持不變**，否則已 grant 的物品會跟主人脫鉤。
   → 好消息：草稿裡的 id（ent_perrin / ent_grix / ent_hostage…）本來就沿用原值，符合要求。

2. **`Character.inventory` 已降級為「投影快取」，不是真實來源。**
   真實來源是 DB 的 items/actor_inventory；`inventory` 是從 DB 重建的名稱清單，只為了不破壞 implausible 閘門。
   → 若之後把 characters.py 的數值塊也外移，**`inventory` 欄位與 `_refresh_inventory_projections` 的呼叫點都要保留**，不能因為「文字 inventory 看起來像可外移的文本」就拔掉。

---

## 安全編輯協定（動手時照做）

1. 改 `game_state.py` 用**精準 Edit**（鎖定 seed_locations / goto_scene / scene 還原那幾行），**不要整檔重寫**。
2. 每次編輯後，目視確認 `seed_items` / `_refresh_inventory_projections`（L858/864/829）三處仍在。
3. 重構後跑回歸：`tests/test_inventory.py` + `tests/test_implausible_gate.py`，確認物品層沒被波及。
4. entity id 一律沿用既有值，不重新命名。
