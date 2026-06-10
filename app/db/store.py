"""SQLite memory store — the durable continuity layer (design §5.3/§6, MVP subset).

This module owns the only DB connection. It persists the event history (incl. AI
prose), the narrative entity registry with state markers, and the dynamic per-scene
summary. The live game mechanics stay in GameState; this DB is "the world remembers".

Connection is lazy and process-wide (single-session MVP). All writes are tiny, local
and synchronous — acceptable for one Discord channel. Tests point `settings.db_path`
at a temp file and call `init_db()` for isolation.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from ..config import settings
from ..content import currency as currency_catalog
from ..content import items as item_catalog
from ..content import quest_taxonomy
from ..logging_setup import get_logger

log = get_logger("db")

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Canonical state vocabularies. Kept here (data layer) so the schema/extractor share
# one source of truth; app/ai/schemas.py imports these for the validated LLM output.
ENTITY_KINDS = ("person", "object", "location", "creature")
ENTITY_STATUSES = ("present", "departed", "hidden", "dead", "destroyed", "unknown")
DISPOSITIONS = ("friendly", "neutral", "wary", "afraid", "hostile", "attack", "cowed")
ITEM_CATEGORIES = ("weapon", "armor", "shield", "consumable", "gear", "treasure", "key_item", "misc")
EQUIP_SLOTS = ("main_hand", "off_hand", "armor", "trinket")

# Entities the narrator should NOT treat as "in the scene right now".
_ABSENT_STATUSES = {"departed", "dead", "destroyed"}

# Locations are a GLOBAL registry, not scoped to a play-scene: a place referenced from
# two different scenes must resolve to one record (so the world can't snap back to a
# scripted scene). They live under this sentinel scope; NPCs/objects stay scoped to the
# location id they are currently in (that scope == the party's current_location_id).
LOCATION_SCOPE = "__world__"

_conn: sqlite3.Connection | None = None
_conn_path: str | None = None
_lock = threading.RLock()


# ───────────────────────── connection ─────────────────────────
def init_db(path: str | Path | None = None) -> None:
    """(Re)open the connection and ensure the schema exists. Idempotent."""
    global _conn, _conn_path
    with _lock:
        target = str(path) if path is not None else str(settings.db_path)
        if _conn is not None and _conn_path == target:
            return
        if _conn is not None:
            _conn.close()
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(target, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        _seed_vehicle_types(_conn)
        _conn.commit()
        _conn_path = target
        log.info("db: initialized at %s", target)


def _c() -> sqlite3.Connection:
    if _conn is None or _conn_path != str(settings.db_path):
        init_db()
    assert _conn is not None
    return _conn


def _seed_vehicle_types(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO vehicle_types (type, modifier) VALUES (?, ?)",
        ("horse", 2.0),
    )


def close() -> None:
    global _conn, _conn_path
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None
        _conn_path = None


def reset_world() -> None:
    """Wipe all continuity tables — used when a fresh campaign starts (/start)."""
    with _lock:
        c = _c()
        c.execute("DELETE FROM location_cards")
        c.execute("DELETE FROM entities")
        c.execute("DELETE FROM scene_state")
        c.execute("DELETE FROM event_log")
        c.execute("DELETE FROM quests")
        c.execute("DELETE FROM memory_chunks")
        c.execute("DELETE FROM mention_tally")
        c.execute("DELETE FROM actor_inventory")
        c.execute("DELETE FROM items")
        c.commit()
        log.info("db: world reset")


def _now() -> float:
    return time.time()


# ───────────────────────── event_log ─────────────────────────
def insert_event(
    *, id: str, scene_id: str | None, actor_id: str, actor_name: str,
    kind: str, summary: str, narration: str, scope: str, data: dict, ts: float,
) -> None:
    with _lock:
        c = _c()
        c.execute(
            "INSERT OR REPLACE INTO event_log "
            "(id, scene_id, actor_id, actor_name, kind, summary, narration, scope, data, ts) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (id, scene_id, actor_id, actor_name, kind, summary, narration, scope,
             json.dumps(data, ensure_ascii=False), ts),
        )
        c.commit()


def update_narration(event_id: str, prose: str) -> None:
    with _lock:
        c = _c()
        c.execute("UPDATE event_log SET narration=? WHERE id=?", (prose, event_id))
        c.commit()


def recent_events(scene_id: str | None, limit: int) -> list[dict]:
    """Return the last `limit` events for the scene, oldest→newest, incl. prose."""
    with _lock:
        c = _c()
        rows = c.execute(
            "SELECT * FROM event_log WHERE scene_id IS ? ORDER BY ts DESC LIMIT ?",
            (scene_id, limit),
        ).fetchall()
    return [_event_to_dict(r) for r in reversed(rows)]


def _event_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["data"] = json.loads(d.get("data") or "{}")
    except json.JSONDecodeError:
        d["data"] = {}
    return d


def _quest_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("seed", "details", "tags"):
        try:
            d[key] = json.loads(d.get(key) or "{}")
        except json.JSONDecodeError:
            d[key] = {}
    d["tags"] = quest_taxonomy.normalize_tags(d.get("tags"))
    return d


def _coerce_item_category(value: str | None) -> str:
    return value if value in ITEM_CATEGORIES else "misc"


def _coerce_equip_slot(value: str | None) -> str | None:
    return value if value in EQUIP_SLOTS else None


def _json_list(value: str | None) -> list:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_dict(value: str | None) -> dict:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _item_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["aliases"] = _json_list(d.get("aliases"))
    d["metadata"] = _json_dict(d.get("metadata"))
    d["stackable"] = bool(d.get("stackable"))
    return d


def _inventory_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["aliases"] = _json_list(d.get("aliases"))
    d["metadata"] = _json_dict(d.get("metadata"))
    d["instance_state"] = _json_dict(d.get("instance_state"))
    d["stackable"] = bool(d.get("stackable"))
    d["equipped"] = bool(d.get("equipped"))
    return d


def _item_aliases(existing: list | None, additions: list | None) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for value in [*(existing or []), *(additions or [])]:
        if not isinstance(value, str):
            continue
        text = value.strip()
        key = item_catalog.normalize_name(text)
        if text and key and key not in seen:
            seen.add(key)
            merged.append(text)
    return merged


def register_item(
    name: str,
    *,
    item_id: str | None = None,
    category: str = "misc",
    slot: str | None = None,
    description: str = "",
    aliases: list[str] | None = None,
    metadata: dict | None = None,
    source: str = "dynamic",
    stackable: bool = True,
) -> str:
    """Register or find a canonical item definition by normalized name."""
    display_name = (name or "").strip()
    if not display_name:
        raise ValueError("item name is required")
    norm = item_catalog.normalize_name(display_name)
    category = _coerce_item_category(category)
    slot = _coerce_equip_slot(slot)
    now = _now()
    with _lock:
        c = _c()
        row = c.execute("SELECT * FROM items WHERE norm_name=?", (norm,)).fetchone()
        if row is None and item_id:
            row = c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if row:
            item = _item_to_dict(row)
            merged_aliases = _item_aliases(item.get("aliases"), aliases)
            if display_name != item.get("name"):
                merged_aliases = _item_aliases(merged_aliases, [display_name])
            merged_metadata = {**(item.get("metadata") or {}), **(metadata or {})}
            c.execute(
                "UPDATE items SET aliases=?, category=?, slot=?, description=?, "
                "stackable=?, metadata=?, source=?, updated_ts=? WHERE id=?",
                (
                    json.dumps(merged_aliases, ensure_ascii=False),
                    category if category != "misc" or item.get("category") == "misc" else item.get("category"),
                    slot if slot is not None else item.get("slot"),
                    description or item.get("description") or "",
                    1 if stackable else 0,
                    json.dumps(merged_metadata, ensure_ascii=False),
                    item.get("source") or source,
                    now,
                    item["id"],
                ),
            )
            c.commit()
            return item["id"]
        new_id = item_id or f"item_{uuid.uuid4().hex[:10]}"
        c.execute(
            "INSERT INTO items (id, norm_name, name, aliases, category, slot, description, "
            "stackable, metadata, source, created_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id,
                norm,
                display_name,
                json.dumps(_item_aliases([], aliases), ensure_ascii=False),
                category,
                slot,
                description or "",
                1 if stackable else 0,
                json.dumps(metadata or {}, ensure_ascii=False),
                source or "dynamic",
                now,
                now,
            ),
        )
        c.commit()
        return new_id


def find_item_by_ref(ref: str) -> dict | None:
    """Resolve an item by id, normalized name, or alias."""
    if not ref or not ref.strip():
        return None
    key = item_catalog.normalize_name(ref)
    with _lock:
        c = _c()
        row = c.execute("SELECT * FROM items WHERE id=? OR norm_name=?", (ref.strip(), key)).fetchone()
        if row:
            return _item_to_dict(row)
        rows = c.execute("SELECT * FROM items ORDER BY created_ts").fetchall()
    for row in rows:
        item = _item_to_dict(row)
        names = [item.get("name"), *(item.get("aliases") or [])]
        if any(item_catalog.normalize_name(n or "") == key for n in names):
            return item
    return None


def grant_item(
    actor_id: str,
    item_ref: str,
    *,
    quantity: int = 1,
    category: str | None = None,
    slot: str | None = None,
    event_id: str | None = None,
    aliases: list[str] | None = None,
    description: str = "",
    metadata: dict | None = None,
    source: str = "dynamic",
    stackable: bool = True,
) -> dict:
    """Grant an item to an actor, lazily creating the catalog row only on acquisition."""
    if not actor_id or not actor_id.strip():
        raise ValueError("actor_id is required")
    currency_grant = currency_catalog.parse_currency_grant(item_ref, quantity)
    if currency_grant:
        coin_def = next(d for d in currency_catalog.coin_item_defs() if d["id"] == currency_grant.item_id)
        item_id = register_item(
            coin_def["name"],
            item_id=coin_def["id"],
            category=coin_def["category"],
            slot=coin_def["slot"],
            aliases=coin_def.get("aliases", []),
            description=coin_def.get("description", ""),
            metadata=coin_def.get("metadata", {}),
            source=coin_def.get("source", "seed"),
            stackable=True,
        )
        item_ref = item_id
        qty = currency_grant.quantity
        category = "treasure"
        stackable = True
    elif currency_catalog.looks_like_currency(item_ref):
        raise ValueError(f"invalid currency grant: {item_ref!r}")
    else:
        qty = max(1, int(quantity or 1))
    item = find_item_by_ref(item_ref)
    if item is None:
        item_id = register_item(
            item_ref,
            category=category or "misc",
            slot=slot,
            aliases=aliases,
            description=description,
            metadata=metadata,
            source=source,
            stackable=stackable,
        )
        item = find_item_by_ref(item_id)
    elif aliases or category or slot or description or metadata:
        register_item(
            item["name"],
            item_id=item["id"],
            category=category or item.get("category") or "misc",
            slot=slot if slot is not None else item.get("slot"),
            aliases=aliases,
            description=description or item.get("description") or "",
            metadata={**(item.get("metadata") or {}), **(metadata or {})},
            source=item.get("source") or source,
            stackable=bool(item.get("stackable", stackable)),
        )
        item = find_item_by_ref(item["id"])
    assert item is not None
    now = _now()
    with _lock:
        c = _c()
        row = c.execute(
            "SELECT * FROM actor_inventory WHERE actor_id=? AND item_id=?",
            (actor_id, item["id"]),
        ).fetchone()
        if row:
            c.execute(
                "UPDATE actor_inventory SET quantity=quantity+?, acquired_event_id=?, acquired_ts=? "
                "WHERE id=?",
                (qty, event_id or row["acquired_event_id"], now, row["id"]),
            )
        else:
            c.execute(
                "INSERT INTO actor_inventory (id, actor_id, item_id, quantity, equipped, "
                "instance_state, acquired_event_id, acquired_ts) VALUES (?,?,?,?,?,?,?,?)",
                (
                    f"inv_{uuid.uuid4().hex[:10]}",
                    actor_id,
                    item["id"],
                    qty,
                    0,
                    "{}",
                    event_id,
                    now,
                ),
            )
        c.commit()
    return get_inventory_item(actor_id, item["id"]) or item


def get_inventory_item(actor_id: str, item_ref: str) -> dict | None:
    item = find_item_by_ref(item_ref)
    if item is None:
        return None
    with _lock:
        c = _c()
        row = c.execute(
            "SELECT ai.*, it.norm_name, it.name, it.aliases, it.category, it.slot, "
            "it.description, it.stackable, it.metadata, it.source "
            "FROM actor_inventory ai JOIN items it ON it.id=ai.item_id "
            "WHERE ai.actor_id=? AND ai.item_id=?",
            (actor_id, item["id"]),
        ).fetchone()
    return _inventory_to_dict(row) if row else None


def get_inventory(actor_id: str) -> list[dict]:
    with _lock:
        c = _c()
        rows = c.execute(
            "SELECT ai.*, it.norm_name, it.name, it.aliases, it.category, it.slot, "
            "it.description, it.stackable, it.metadata, it.source "
            "FROM actor_inventory ai JOIN items it ON it.id=ai.item_id "
            "WHERE ai.actor_id=? ORDER BY it.category, it.name",
            (actor_id,),
        ).fetchall()
    return [_inventory_to_dict(r) for r in rows]


def project_inventory(actor_id: str) -> list[str]:
    """Legacy Character.inventory projection used by the implausible gate."""
    names: list[str] = []
    for row in get_inventory(actor_id):
        qty = int(row.get("quantity") or 1)
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        label = str(metadata.get("projection_name") or row.get("name") or "")
        if qty > 1:
            label = f"{label} x{qty}"
        if label:
            names.append(label)
    return names


def remove_item(actor_id: str, item_ref: str, quantity: int = 1) -> bool:
    item = find_item_by_ref(item_ref)
    if item is None:
        return False
    qty = max(1, int(quantity or 1))
    with _lock:
        c = _c()
        row = c.execute(
            "SELECT * FROM actor_inventory WHERE actor_id=? AND item_id=?",
            (actor_id, item["id"]),
        ).fetchone()
        if not row:
            return False
        remaining = int(row["quantity"] or 0) - qty
        if remaining > 0:
            c.execute("UPDATE actor_inventory SET quantity=? WHERE id=?", (remaining, row["id"]))
        else:
            c.execute("DELETE FROM actor_inventory WHERE id=?", (row["id"],))
        c.commit()
    return True


def transfer_item(src_actor_id: str, dst_actor_id: str, item_ref: str, quantity: int = 1) -> bool:
    item = find_item_by_ref(item_ref)
    if item is None:
        return False
    inv = get_inventory_item(src_actor_id, item["id"])
    if not inv or int(inv.get("quantity") or 0) < max(1, int(quantity or 1)):
        return False
    remove_item(src_actor_id, item["id"], quantity)
    grant_item(dst_actor_id, item["id"], quantity=quantity)
    return True


def set_equipped(actor_id: str, item_ref: str, equipped: bool) -> bool:
    item = find_item_by_ref(item_ref)
    if item is None:
        return False
    if item.get("slot") not in EQUIP_SLOTS:
        return False
    with _lock:
        c = _c()
        row = c.execute(
            "SELECT id FROM actor_inventory WHERE actor_id=? AND item_id=?",
            (actor_id, item["id"]),
        ).fetchone()
        if not row:
            return False
        c.execute("UPDATE actor_inventory SET equipped=? WHERE id=?", (1 if equipped else 0, row["id"]))
        c.commit()
    return True


def seed_items(defs: list[dict]) -> None:
    """Insert authored item definitions without overwriting runtime quantities."""
    for d in defs:
        name = d.get("name")
        if not name:
            continue
        register_item(
            name,
            item_id=d.get("id"),
            category=d.get("category", "misc"),
            slot=d.get("slot"),
            description=d.get("description", ""),
            aliases=d.get("aliases", []),
            metadata=d.get("metadata", {}),
            source=d.get("source", "seed"),
            stackable=bool(d.get("stackable", True)),
        )


def fallback_quest_details(seed: dict) -> dict:
    """Build a minimal executable quest card from a quest seed."""
    return {
        "title": seed.get("title_hint") or "Unnamed Quest",
        "giver": seed.get("giver") or "",
        "objective": seed.get("objective_hint") or seed.get("premise") or "",
        "known_info": [x for x in [
            seed.get("premise"),
            seed.get("known_constraints"),
            f"Stakes: {seed.get('stakes')}" if seed.get("stakes") else "",
        ] if x],
        "details": [seed.get("premise") or seed.get("objective_hint") or ""],
        "next_steps": [seed.get("objective_hint") or "Follow up with the quest giver."],
        "success_conditions": [seed.get("objective_hint") or "Resolve the request."],
        "failure_risks": [seed.get("stakes") or "The opportunity may worsen or expire."],
        "reward": seed.get("reward_hint") or "",
        "tags": quest_taxonomy.normalize_tags(seed.get("tags")),
    }


def upsert_quest_seed(
    *,
    dedupe_key: str,
    seed: dict,
    source_event_id: str | None = None,
    scene_id: str | None = None,
    giver: str = "",
    status: str = "available",
    visibility: str = "summary",
    detail_state: str = "pending_agent",
) -> dict:
    """Create or refresh a quest from a GM quest seed."""
    key = (dedupe_key or "").strip() or f"quest_{uuid.uuid4().hex[:10]}"
    if status not in quest_taxonomy.QUEST_STATUSES:
        status = "available"
    if detail_state not in quest_taxonomy.QUEST_DETAIL_STATES:
        detail_state = "pending_agent"
    tags = quest_taxonomy.normalize_tags((seed or {}).get("tags"))
    now = _now()
    with _lock:
        c = _c()
        row = c.execute("SELECT * FROM quests WHERE dedupe_key=?", (key,)).fetchone()
        if row:
            quest_id = row["id"]
            current = _quest_to_dict(row)
            if current.get("status") in ("accepted", "completed", "failed", "expired"):
                status = current["status"]
            if current.get("detail_state") == "ready":
                detail_state = "ready"
            c.execute(
                "UPDATE quests SET source_event_id=?, scene_id=?, giver=?, status=?, "
                "visibility=?, seed=?, tags=?, detail_state=?, updated_ts=? WHERE id=?",
                (
                    source_event_id or row["source_event_id"],
                    scene_id or row["scene_id"],
                    giver or row["giver"],
                    status,
                    visibility,
                    json.dumps(seed or {}, ensure_ascii=False),
                    json.dumps(tags, ensure_ascii=False),
                    detail_state,
                    now,
                    quest_id,
                ),
            )
        else:
            quest_id = f"quest_{uuid.uuid4().hex[:10]}"
            c.execute(
                "INSERT INTO quests (id, dedupe_key, source_event_id, scene_id, giver, "
                "status, visibility, seed, details, tags, detail_state, created_ts, updated_ts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    quest_id,
                    key,
                    source_event_id,
                    scene_id,
                    giver,
                    status,
                    visibility,
                    json.dumps(seed or {}, ensure_ascii=False),
                    "{}",
                    json.dumps(tags, ensure_ascii=False),
                    detail_state,
                    now,
                    now,
                ),
            )
        c.commit()
        saved = c.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
    return _quest_to_dict(saved)  # type: ignore[arg-type]


def set_quest_details(quest_id: str, details: dict, *, detail_state: str = "ready") -> dict | None:
    if detail_state not in quest_taxonomy.QUEST_DETAIL_STATES:
        detail_state = "ready"
    tags = quest_taxonomy.normalize_tags((details or {}).get("tags"))
    with _lock:
        c = _c()
        row = c.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
        if not row:
            return None
        current = _quest_to_dict(row)
        merged_tags = quest_taxonomy.normalize_tags({**current.get("tags", {}), **tags})
        c.execute(
            "UPDATE quests SET details=?, tags=?, detail_state=?, updated_ts=? WHERE id=?",
            (
                json.dumps(details or {}, ensure_ascii=False),
                json.dumps(merged_tags, ensure_ascii=False),
                detail_state,
                _now(),
                quest_id,
            ),
        )
        c.commit()
        saved = c.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
    return _quest_to_dict(saved) if saved else None


def update_quest_status(quest_id: str, status: str) -> dict | None:
    if status not in quest_taxonomy.QUEST_STATUSES:
        return None
    with _lock:
        c = _c()
        if c.execute("SELECT id FROM quests WHERE id=?", (quest_id,)).fetchone() is None:
            return None
        c.execute("UPDATE quests SET status=?, updated_ts=? WHERE id=?", (status, _now(), quest_id))
        c.commit()
        saved = c.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
    return _quest_to_dict(saved) if saved else None


def accept_quest(quest_id: str) -> dict | None:
    quest = update_quest_status(quest_id, "accepted")
    if quest and quest.get("detail_state") == "pending_agent" and not quest.get("details"):
        return set_quest_details(
            quest_id,
            fallback_quest_details(quest.get("seed") or {}),
            detail_state="details_degraded",
        )
    return quest


def get_quest(quest_id: str) -> dict | None:
    with _lock:
        c = _c()
        row = c.execute("SELECT * FROM quests WHERE id=?", (quest_id,)).fetchone()
    return _quest_to_dict(row) if row else None


def list_quests(*, scene_id: str | None = None) -> list[dict]:
    with _lock:
        c = _c()
        if scene_id is None:
            rows = c.execute("SELECT * FROM quests ORDER BY created_ts").fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM quests WHERE scene_id IS ? ORDER BY created_ts", (scene_id,)
            ).fetchall()
    return [_quest_to_dict(r) for r in rows]


# ───────────────────────── entities ─────────────────────────
def _entity_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    for key in ("aliases", "flags"):
        try:
            d[key] = json.loads(d.get(key) or ("[]" if key == "aliases" else "{}"))
        except json.JSONDecodeError:
            d[key] = [] if key == "aliases" else {}
    return d


def upsert_entity(
    *, id: str, scene_id: str | None, kind: str, name: str,
    aliases: list[str] | None = None, status: str = "present",
    location_id: str | None = None, disposition: str | None = None,
    flags: dict | None = None, notes: str = "", first_seen_event_id: str | None = None,
) -> None:
    kind = kind if kind in ENTITY_KINDS else "person"
    status = status if status in ENTITY_STATUSES else "present"
    disposition = disposition if disposition in DISPOSITIONS else None
    if kind == "location":
        anchor_id = scene_id if scene_id not in (None, LOCATION_SCOPE) else None
        flags = _ensure_location_coordinate_flags(id, flags or {}, anchor_id=anchor_id)
    now = _now()
    with _lock:
        c = _c()
        existing = c.execute("SELECT id FROM entities WHERE id=?", (id,)).fetchone()
        if existing:
            c.execute(
                "UPDATE entities SET scene_id=?, kind=?, name=?, aliases=?, status=?, "
                "location_id=?, disposition=?, flags=?, notes=?, updated_ts=? WHERE id=?",
                (scene_id, kind, name, json.dumps(aliases or [], ensure_ascii=False), status,
                 location_id, disposition, json.dumps(flags or {}, ensure_ascii=False),
                 notes, now, id),
            )
        else:
            c.execute(
                "INSERT INTO entities (id, scene_id, kind, name, aliases, status, location_id, "
                "disposition, flags, notes, first_seen_event_id, created_ts, updated_ts) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (id, scene_id, kind, name, json.dumps(aliases or [], ensure_ascii=False), status,
                 location_id, disposition, json.dumps(flags or {}, ensure_ascii=False), notes,
                 first_seen_event_id, now, now),
            )
        c.commit()


def merge_entity_flags(ent_id: str, updates: dict | None = None, *, remove: list[str] | None = None) -> dict | None:
    """Merge a small patch into an entity's JSON flags and return the saved flags."""
    ent = get_entity_by_id(ent_id)
    if ent is None:
        return None
    flags = dict(ent.get("flags") or {})
    for key, value in (updates or {}).items():
        if value is None:
            flags.pop(key, None)
        else:
            flags[key] = value
    for key in remove or []:
        flags.pop(key, None)
    upsert_entity(
        id=ent["id"], scene_id=ent["scene_id"], kind=ent["kind"], name=ent["name"],
        aliases=ent.get("aliases", []), status=ent["status"],
        location_id=ent.get("location_id"), disposition=ent.get("disposition"),
        flags=flags, notes=ent.get("notes", ""),
        first_seen_event_id=ent.get("first_seen_event_id"),
    )
    return flags


def seed_entities(scene_id: str, defs: list[dict]) -> None:
    """Register a scene's authored entities. Only inserts ones not already present,
    so runtime state survives a scene revisit."""
    with _lock:
        c = _c()
        for i, d in enumerate(defs):
            ent_id = d.get("id") or f"ent_{scene_id}_{i}"
            if c.execute("SELECT id FROM entities WHERE id=?", (ent_id,)).fetchone():
                continue
            upsert_entity(
                id=ent_id, scene_id=scene_id, kind=d.get("kind", "person"),
                name=d["name"], aliases=d.get("aliases", []),
                status=d.get("status", "present"), location_id=d.get("location_id"),
                disposition=d.get("disposition"), flags=d.get("flags", {}),
                notes=d.get("notes", ""),
            )


def get_all(scene_id: str | None) -> list[dict]:
    with _lock:
        c = _c()
        rows = c.execute(
            "SELECT * FROM entities WHERE scene_id IS ? ORDER BY created_ts", (scene_id,)
        ).fetchall()
    return [_entity_to_dict(r) for r in rows]


def get_present(scene_id: str | None) -> list[dict]:
    """Entities the narrator may place in the scene right now (not departed/dead/destroyed)."""
    return [e for e in get_all(scene_id) if e["status"] not in _ABSENT_STATUSES]


def find_by_ref(scene_id: str | None, ref: str) -> dict | None:
    """Resolve a free-text reference (name or alias) to an entity in the scene."""
    if not ref:
        return None
    ref_l = ref.strip().lower()
    candidates = get_all(scene_id)
    for e in candidates:
        if e["name"].strip().lower() == ref_l:
            return e
    for e in candidates:
        names = [e["name"]] + list(e.get("aliases") or [])
        if any(ref_l in n.lower() or n.lower() in ref_l for n in names if n):
            return e
    return None


def get_entity_by_id(ent_id: str) -> dict | None:
    if not ent_id:
        return None
    with _lock:
        c = _c()
        row = c.execute("SELECT * FROM entities WHERE id=?", (ent_id,)).fetchone()
    return _entity_to_dict(row) if row else None


def transiting_entities() -> list[dict]:
    """Entities currently scoped to a connection and carrying flags.transit."""
    with _lock:
        c = _c()
        rows = c.execute(
            "SELECT * FROM entities WHERE flags LIKE ? ORDER BY created_ts",
            ('%"transit"%',),
        ).fetchall()
    entities = [_entity_to_dict(r) for r in rows]
    return [
        e for e in entities
        if isinstance((e.get("flags") or {}).get("transit"), dict)
    ]


def get_locations() -> list[dict]:
    """The global location registry (all kind='location' entities, any scope)."""
    with _lock:
        c = _c()
        rows = c.execute(
            "SELECT * FROM entities WHERE kind='location' ORDER BY created_ts"
        ).fetchall()
    return [_entity_to_dict(r) for r in rows]


def find_location(ref: str) -> dict | None:
    """Resolve a free-text place reference against the GLOBAL location registry
    (name then alias/substring), so the same place can't fork into duplicates."""
    if not ref or not ref.strip():
        return None
    ref_l = ref.strip().lower()
    locs = get_locations()
    for e in locs:
        if e["name"].strip().lower() == ref_l:
            return e
    for e in locs:
        names = [e["name"]] + list(e.get("aliases") or [])
        if any(ref_l in n.lower() or n.lower() in ref_l for n in names if n):
            return e
    return None


_COORD_GRID_KM = 5.0


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _has_coordinates(flags: dict) -> bool:
    return _float_or_none(flags.get("x")) is not None and _float_or_none(flags.get("y")) is not None


def _coord_key(x: float, y: float) -> tuple[float, float]:
    return (round(x, 3), round(y, 3))


def _coord_parent_of(loc: dict | None) -> str | None:
    if not loc:
        return None
    flags = loc.get("flags") or {}
    parent = flags.get("coord_parent")
    if isinstance(parent, str) and parent.strip():
        return parent
    return loc.get("id")


def _anchor_xy(anchor: dict | None, coord_parent: str | None) -> tuple[float, float]:
    if not anchor:
        return 0.0, 0.0
    flags = anchor.get("flags") or {}
    if flags.get("coord_parent") != coord_parent:
        return 0.0, 0.0
    x = _float_or_none(flags.get("x"))
    y = _float_or_none(flags.get("y"))
    if x is None or y is None:
        return 0.0, 0.0
    return x, y


def _occupied_coordinates(coord_parent: str | None, *, exclude_id: str | None = None) -> set[tuple[float, float]]:
    occupied: set[tuple[float, float]] = set()
    for loc in get_locations():
        if exclude_id and loc.get("id") == exclude_id:
            continue
        flags = loc.get("flags") or {}
        if flags.get("coord_parent") != coord_parent:
            continue
        x = _float_or_none(flags.get("x"))
        y = _float_or_none(flags.get("y"))
        if x is not None and y is not None:
            occupied.add(_coord_key(x, y))
    return occupied


def _grid_offsets() -> list[tuple[float, float]]:
    step = _COORD_GRID_KM
    offsets = [(0.0, 0.0)]
    for ring in range(1, 20):
        d = ring * step
        offsets.extend([
            (d, 0.0), (-d, 0.0), (0.0, d), (0.0, -d),
            (d, d), (d, -d), (-d, d), (-d, -d),
        ])
    return offsets


def _ensure_location_coordinate_flags(
    ent_id: str,
    flags: dict | None,
    *,
    anchor_id: str | None = None,
    coord_parent: str | None = None,
) -> dict:
    """Ensure backend-only local coordinates exist on location flags."""
    saved = dict(flags or {})
    if _has_coordinates(saved):
        if coord_parent and not saved.get("coord_parent"):
            saved["coord_parent"] = coord_parent
        return saved

    anchor = get_entity_by_id(anchor_id) if anchor_id else None
    parent = coord_parent or saved.get("coord_parent") or saved.get("parent")
    if not parent and anchor:
        parent = _coord_parent_of(anchor)
    parent = str(parent) if parent else None

    base_x, base_y = _anchor_xy(anchor, parent)
    occupied = _occupied_coordinates(parent, exclude_id=ent_id)
    for dx, dy in _grid_offsets():
        x = base_x + dx
        y = base_y + dy
        if _coord_key(x, y) not in occupied:
            saved["x"] = x
            saved["y"] = y
            if parent:
                saved["coord_parent"] = parent
            else:
                saved.pop("coord_parent", None)
            return saved

    saved["x"] = base_x
    saved["y"] = base_y
    if parent:
        saved["coord_parent"] = parent
    return saved


def ensure_location_coordinates(
    location_id: str,
    *,
    anchor_id: str | None = None,
    coord_parent: str | None = None,
) -> dict | None:
    loc = get_entity_by_id(location_id)
    if not loc or loc.get("kind") != "location":
        return None
    flags = _ensure_location_coordinate_flags(
        loc["id"], loc.get("flags") or {}, anchor_id=anchor_id, coord_parent=coord_parent
    )
    if flags == (loc.get("flags") or {}):
        return loc
    upsert_entity(
        id=loc["id"], scene_id=loc["scene_id"], kind=loc["kind"], name=loc["name"],
        aliases=loc.get("aliases", []), status=loc["status"],
        location_id=loc.get("location_id"), disposition=loc.get("disposition"),
        flags=flags, notes=loc.get("notes", ""),
        first_seen_event_id=loc.get("first_seen_event_id"),
    )
    return get_entity_by_id(location_id)


def register_location(name: str, *, location_id: str | None = None,
                      aliases: list[str] | None = None, notes: str = "",
                      flags: dict | None = None,
                      coordinate_anchor_id: str | None = None,
                      coord_parent: str | None = None) -> dict:
    """Create (or refresh) a global location entity and return it. `flags` may carry
    adjacency: {"connects": [loc_ids], "parent": loc_id} — used to bound travel."""
    ent_id = location_id or f"loc_{uuid.uuid4().hex[:10]}"
    existing = get_entity_by_id(ent_id)
    merged_flags = dict((existing or {}).get("flags") or {})
    merged_flags.update(flags or {})
    merged_flags = _ensure_location_coordinate_flags(
        ent_id, merged_flags, anchor_id=coordinate_anchor_id, coord_parent=coord_parent
    )
    upsert_entity(
        id=ent_id, scene_id=LOCATION_SCOPE, kind="location", name=name.strip(),
        aliases=aliases or [], status="present", notes=notes, flags=merged_flags,
    )
    return get_entity_by_id(ent_id)  # type: ignore[return-value]


# Keys that shape the hierarchical world graph; stored under entity flags so SQLite needs
# no schema change (a later Postgres pass can split them into columns). See scenario.LOCATIONS.
_GRAPH_FLAG_KEYS = (
    "loc_type", "parent", "connects", "travel_cost", "danger",
    "required_rank", "gate", "gate_reason", "biome", "controlling_faction",
    "distances", "terrain_modifier", "movement_base", "is_vehicle", "vehicle_type",
    "x", "y", "coord_parent",
)


def seed_locations(defs: list[dict]) -> None:
    """Register a campaign's authored locations once (id-stable, insert-only) so travel
    resolves to canonical ids instead of forking ad-hoc duplicates. Carries the
    hierarchical-world-graph fields (loc_type / parent / connects / travel_cost / danger …)
    into flags so pathfinding and the access gate can read them."""
    for d in defs:
        if d.get("id") and (existing := get_entity_by_id(d["id"])):
            flags = dict(existing.get("flags") or {})
            for k in _GRAPH_FLAG_KEYS:
                if d.get(k) is not None and flags.get(k) is None:
                    value = d[k]
                    flags[k] = dict(value) if isinstance(value, dict) else list(value) if isinstance(value, list) else value
            flags = _ensure_location_coordinate_flags(existing["id"], flags)
            if flags != (existing.get("flags") or {}):
                upsert_entity(
                    id=existing["id"], scene_id=existing["scene_id"], kind=existing["kind"],
                    name=existing["name"], aliases=existing.get("aliases", []),
                    status=existing["status"], location_id=existing.get("location_id"),
                    disposition=existing.get("disposition"), flags=flags,
                    notes=existing.get("notes", ""),
                    first_seen_event_id=existing.get("first_seen_event_id"),
                )
            continue
        flags = {k: d[k] for k in _GRAPH_FLAG_KEYS if d.get(k) is not None}
        if isinstance(flags.get("connects"), list):
            flags["connects"] = list(flags["connects"])
        if isinstance(flags.get("distances"), dict):
            flags["distances"] = dict(flags["distances"])
        register_location(
            d["name"], location_id=d.get("id"),
            aliases=d.get("aliases", []), notes=d.get("notes", ""),
            flags=flags or None,
        )


def move_entity(ent_id: str, dest_location_id: str) -> bool:
    """Relocate an entity to another registered location (design: NPCs can roam). Presence
    is scoped by scene_id, so a move re-scopes the entity there and keeps location_id in
    sync. Returns True if moved. The destination must be a known location."""
    ent = get_entity_by_id(ent_id)
    if not ent:
        return False
    dest = get_entity_by_id(dest_location_id) or find_location(dest_location_id)
    if not dest or dest.get("kind") != "location":
        return False
    flags = dict(ent.get("flags") or {})
    flags.pop("transit", None)
    upsert_entity(
        id=ent["id"], scene_id=dest["id"], kind=ent["kind"], name=ent["name"],
        aliases=ent.get("aliases", []), status=ent["status"], location_id=dest["id"],
        disposition=ent.get("disposition"), flags=flags,
        notes=ent.get("notes", ""), first_seen_event_id=ent.get("first_seen_event_id"),
    )
    return True


def vehicle_modifier(vehicle_type: str | None) -> float:
    if not vehicle_type:
        return 1.0
    with _lock:
        c = _c()
        row = c.execute(
            "SELECT modifier FROM vehicle_types WHERE type=?",
            (str(vehicle_type),),
        ).fetchone()
    if row is None:
        return 1.0
    try:
        return float(row["modifier"])
    except (TypeError, ValueError):
        return 1.0


def resolve_or_register_location(ref: str) -> dict | None:
    """Travel helper (design: location is first-class state). Resolve a free-text place
    reference against the global registry; if none exists, create one immediately — a
    player's explicit travel target is authoritative (threshold 1, vs the AI-mention
    threshold in record_mention). Returns the location entity dict, or None for empty ref."""
    if not ref or not ref.strip():
        return None
    return find_location(ref) or register_location(ref.strip())


# ───────────────────────── hierarchical world graph (design §6) ─────────────────────────
def _neighbor_ids(loc_id: str, by_id: dict[str, dict]) -> list[str]:
    """Immediate graph neighbours of a location, in a DETERMINISTIC order so pathfinding
    is reproducible: lateral `connects` (declared order) → children (registry order) → parent.
    Edges are bidirectional in the data (connects declared symmetrically; child↔parent both
    covered here), so callers need not synthesise reverse edges."""
    here = by_id.get(loc_id)
    if not here:
        return []
    flags = here.get("flags") or {}
    out: list[str] = list(flags.get("connects") or [])
    out += [lid for lid, loc in by_id.items()
            if (loc.get("flags") or {}).get("parent") == loc_id]
    parent = flags.get("parent")
    if parent:
        out.append(parent)
    seen: set[str] = set()
    res: list[str] = []
    for nid in out:
        if nid and nid != loc_id and nid in by_id and nid not in seen:
            seen.add(nid)
            res.append(nid)
    return res


def travel_options(loc_id: str) -> list[dict]:
    """Locations one hop from `loc_id` in the world graph (parent / children / connects),
    as entity dicts. Drives the AI's known exits and any 'where can I go' UI."""
    by_id = {l["id"]: l for l in get_locations()}
    return [by_id[nid] for nid in _neighbor_ids(loc_id, by_id)]


def travel_path(src_id: str, dst_id: str) -> list[str] | None:
    """Shortest node path through the world graph from `src_id` to `dst_id`, EXCLUDING the
    source and INCLUDING the destination. [] if already there; None if either id is unknown
    or the two are in disconnected components. Hop-count BFS — travel_cost is applied per
    traversed node by the caller (so distant places cost time but stay reachable, no teleport)."""
    by_id = {l["id"]: l for l in get_locations()}
    if src_id not in by_id or dst_id not in by_id:
        return None
    if src_id == dst_id:
        return []
    prev: dict[str, str | None] = {src_id: None}
    queue: list[str] = [src_id]
    head = 0
    while head < len(queue):
        cur = queue[head]
        head += 1
        if cur == dst_id:
            break
        for nb in _neighbor_ids(cur, by_id):
            if nb not in prev:
                prev[nb] = cur
                queue.append(nb)
    if dst_id not in prev:
        return None
    path: list[str] = []
    node: str | None = dst_id
    while node is not None and node != src_id:
        path.append(node)
        node = prev[node]
    path.reverse()
    return path


def location_access(loc_id: str) -> dict:
    """Access gate for ENTERING a location (design §12 region gating, mixed policy).
    Pure data read — the caller decides messaging. Returns
    {gate: free|soft|hard, danger: int, required_rank: str|None, reason: str}.
    `required_rank` or explicit gate='hard' hard-blocks; danger≥3 (or gate='soft') soft-warns;
    otherwise free. (MVP has no guild rank, so any required_rank currently hard-blocks.)"""
    loc = get_entity_by_id(loc_id)
    flags = (loc or {}).get("flags") or {}
    danger = int(flags.get("danger") or 0)
    req = flags.get("required_rank")
    explicit = flags.get("gate")
    reason = str(flags.get("gate_reason") or "")
    if explicit == "hard" or req:
        return {"gate": "hard", "danger": danger, "required_rank": req, "reason": reason}
    if explicit == "soft" or danger >= 3:
        return {"gate": "soft", "danger": danger, "required_rank": None, "reason": reason}
    return {"gate": "free", "danger": danger, "required_rank": None, "reason": reason}


def location_travel_cost(loc_id: str) -> int:
    """Day-stages spent entering `loc_id` (default 1; authored 0 for in-town hops)."""
    loc = get_entity_by_id(loc_id)
    flags = (loc or {}).get("flags") or {}
    raw = flags.get("travel_cost")
    return int(raw) if isinstance(raw, (int, float)) else 1


# ───────────────────────── mention tally (debounced auto-register) ─────────────────────────
def register_combatant(
    scene_id: str | None, name: str, kind: str, *,
    disposition: str = "neutral", aliases: list[str] | None = None, note: str = "",
) -> str | None:
    """Register a present, combat-capable entity IMMEDIATELY — bypassing the AI-mention
    debounce (record_mention threshold). Reserved for the moment a foe actually enters
    play: an AI-narrated hostile, or an explicit player attack on an as-yet-unregistered
    target. A player's blade landing on someone is authoritative the same way a travel
    target is (see resolve_or_register_location). Returns the entity id (existing or new),
    or None for an empty name. No-op-merges onto an existing ref rather than duplicating."""
    if not name or not name.strip():
        return None
    existing = find_by_ref(scene_id, name)
    if existing is not None:
        return existing["id"]
    ent_id = f"ent_{uuid.uuid4().hex[:10]}"
    upsert_entity(
        id=ent_id, scene_id=scene_id,
        kind=kind if kind in ENTITY_KINDS else "creature",
        name=name.strip(), aliases=aliases or [], status="present",
        disposition=disposition if disposition in DISPOSITIONS else "neutral",
        notes=note or "",
    )
    return ent_id


def record_mention(scene_id: str | None, name: str, kind: str) -> int:
    """Count one prose mention of a not-yet-registered entity in this location scope.
    Returns the new running count, or 0 if `name` already maps to a registered entity
    (a scene-scoped one, or a global location) — in which case the caller does nothing."""
    if not name or not name.strip():
        return 0
    if find_by_ref(scene_id, name) is not None:
        return 0
    if kind == "location" and find_location(name) is not None:
        return 0
    norm = name.strip().lower()
    now = _now()
    with _lock:
        c = _c()
        row = c.execute(
            "SELECT count FROM mention_tally WHERE scene_id IS ? AND norm_name=?",
            (scene_id, norm),
        ).fetchone()
        count = (row["count"] if row else 0) + 1
        if row:
            c.execute(
                "UPDATE mention_tally SET count=?, last_ts=?, display_name=?, kind=? "
                "WHERE scene_id IS ? AND norm_name=?",
                (count, now, name.strip(), kind, scene_id, norm),
            )
        else:
            c.execute(
                "INSERT INTO mention_tally (scene_id, norm_name, display_name, kind, count, last_ts) "
                "VALUES (?,?,?,?,?,?)",
                (scene_id, norm, name.strip(), kind, count, now),
            )
        c.commit()
    return count


def promote_mention(scene_id: str | None, name: str, kind: str) -> str | None:
    """Promote a tallied candidate into a real entity and clear its tally row. Locations
    go to the global registry; everything else is scoped to the current location."""
    if not name or not name.strip():
        return None
    norm = name.strip().lower()
    if kind == "location":
        ent_id = register_location(name.strip())["id"]
    else:
        ent_id = f"ent_{uuid.uuid4().hex[:10]}"
        upsert_entity(
            id=ent_id, scene_id=scene_id,
            kind=kind if kind in ENTITY_KINDS else "person",
            name=name.strip(), status="present",
        )
    with _lock:
        c = _c()
        c.execute(
            "DELETE FROM mention_tally WHERE scene_id IS ? AND norm_name=?", (scene_id, norm)
        )
        c.commit()
    return ent_id


# ───────────────────────── conditions (mechanical flags on entities) ─────────────────────────
# Conditions are stored inside the existing `flags` JSON column. Two keys:
#   "conditions":     list[str] — active condition ids (incl. parametric "loyal_to:X")
#   "condition_meta": dict[str, dict] — per-condition data (level, source, duration)
# This keeps the schema stable while letting the engine reason about leveled and
# time-bounded effects.
_CONDITIONS_KEY = "conditions"
_CONDITION_META_KEY = "condition_meta"


def _flags_with_conditions(ent: dict) -> dict:
    flags = dict(ent.get("flags") or {})
    raw = flags.get(_CONDITIONS_KEY) or []
    seen: list[str] = []
    for cid in raw:
        if isinstance(cid, str) and cid and cid not in seen:
            seen.append(cid)
    flags[_CONDITIONS_KEY] = seen
    meta = flags.get(_CONDITION_META_KEY) or {}
    flags[_CONDITION_META_KEY] = {k: dict(v) for k, v in meta.items() if isinstance(v, dict)}
    return flags


def get_conditions(ent_id: str) -> list[str]:
    ent = get_entity_by_id(ent_id)
    if not ent:
        return []
    return list(_flags_with_conditions(ent).get(_CONDITIONS_KEY, []))


def get_condition_meta(ent_id: str) -> dict[str, dict]:
    ent = get_entity_by_id(ent_id)
    if not ent:
        return {}
    return dict(_flags_with_conditions(ent).get(_CONDITION_META_KEY, {}))


def has_condition(ent_id: str, condition_id: str) -> bool:
    return condition_id in get_conditions(ent_id)


def _write_flags(ent: dict, conds: list[str], meta: dict[str, dict]) -> None:
    flags = dict(ent.get("flags") or {})
    flags[_CONDITIONS_KEY] = list(conds)
    flags[_CONDITION_META_KEY] = {k: dict(v) for k, v in meta.items() if isinstance(v, dict)}
    upsert_entity(
        id=ent["id"], scene_id=ent["scene_id"], kind=ent["kind"], name=ent["name"],
        aliases=ent.get("aliases", []), status=ent["status"],
        location_id=ent.get("location_id"), disposition=ent.get("disposition"),
        flags=flags, notes=ent.get("notes", ""),
        first_seen_event_id=ent.get("first_seen_event_id"),
    )


def add_condition(
    ent_id: str,
    condition_id: str,
    *,
    level: int | None = None,
    source: str | None = None,
    duration: int | None = None,
) -> bool:
    """Attach a condition to an entity, optionally with meta. Returns True if newly added
    (or if meta was supplied for an existing condition — meta is merged in-place)."""
    ent = get_entity_by_id(ent_id)
    if not ent or not condition_id:
        return False
    bundle = _flags_with_conditions(ent)
    conds = list(bundle.get(_CONDITIONS_KEY, []))
    meta = dict(bundle.get(_CONDITION_META_KEY, {}))
    newly_added = condition_id not in conds
    if newly_added:
        conds.append(condition_id)

    if level is not None or source is not None or duration is not None:
        slot = dict(meta.get(condition_id, {}))
        if level is not None:
            slot["level"] = int(level)
        if source is not None:
            slot["source"] = source
        if duration is not None:
            slot["duration"] = int(duration)
        meta[condition_id] = slot
    elif newly_added and condition_id not in meta:
        # Keep meta in sync (empty slot) so later upgrades work without re-fetching.
        meta[condition_id] = {}

    if not newly_added and meta == bundle.get(_CONDITION_META_KEY, {}):
        return False
    _write_flags(ent, conds, meta)
    log.info("entity %s gained condition %s meta=%s", ent_id, condition_id, meta.get(condition_id))
    return True


def remove_condition(ent_id: str, condition_id: str) -> bool:
    """Detach a condition from an entity. Returns True if it was present."""
    ent = get_entity_by_id(ent_id)
    if not ent:
        return False
    bundle = _flags_with_conditions(ent)
    conds = list(bundle.get(_CONDITIONS_KEY, []))
    meta = dict(bundle.get(_CONDITION_META_KEY, {}))
    if condition_id not in conds:
        return False
    conds = [c for c in conds if c != condition_id]
    meta.pop(condition_id, None)
    _write_flags(ent, conds, meta)
    log.info("entity %s lost condition %s", ent_id, condition_id)
    return True


def tick_conditions(ent_id: str) -> list[str]:
    """Advance every duration-bound condition by one turn; remove ones that hit zero.
    Returns the list of condition ids that expired."""
    ent = get_entity_by_id(ent_id)
    if not ent:
        return []
    bundle = _flags_with_conditions(ent)
    conds = list(bundle.get(_CONDITIONS_KEY, []))
    # Deep copy so mutating `meta[cid]["duration"]` doesn't silently update the
    # bundle reference and defeat the dirty check below.
    meta = {k: dict(v) for k, v in (bundle.get(_CONDITION_META_KEY, {}) or {}).items()}
    expired: list[str] = []
    mutated = False
    for cid in list(conds):
        slot = meta.get(cid) or {}
        if "duration" not in slot:
            continue
        d = int(slot["duration"]) - 1
        mutated = True
        if d <= 0:
            expired.append(cid)
            conds.remove(cid)
            meta.pop(cid, None)
        else:
            slot["duration"] = d
            meta[cid] = slot
    if mutated:
        _write_flags(ent, conds, meta)
    return expired


def get_conditions_by_ref(scene_id: str | None, ref: str) -> tuple[str | None, list[str]]:
    """Resolve a free-text target reference and return (entity_id, conditions)."""
    ent = find_by_ref(scene_id, ref)
    if not ent:
        return None, []
    return ent["id"], list(_flags_with_conditions(ent).get(_CONDITIONS_KEY, []))


def get_meta_by_ref(scene_id: str | None, ref: str) -> dict[str, dict]:
    ent = find_by_ref(scene_id, ref)
    if not ent:
        return {}
    return dict(_flags_with_conditions(ent).get(_CONDITION_META_KEY, {}))


# Keep accumulated notes bounded so a long-lived entity (or place) can't grow an
# ever-expanding fact log that bloats prompts. We pin the FIRST line (seed identity)
# and keep the most recent facts after it.
_MAX_NOTE_LINES = 6


def _cap_notes(notes: str) -> str:
    lines = [ln for ln in (notes or "").split("\n") if ln.strip()]
    if len(lines) <= _MAX_NOTE_LINES:
        return "\n".join(lines)
    return "\n".join([lines[0]] + lines[-(_MAX_NOTE_LINES - 1):])


def apply_delta(scene_id: str | None, delta: dict) -> str | None:
    """Apply one validated state delta. Resolves entity by ref; can register a new
    one when `register_kind` is given. Returns the affected entity id (or None).

    Recognised keys: entity_ref, status, disposition, location_id, note,
    register_kind, register_name, aliases.
    """
    ref = delta.get("entity_ref") or delta.get("register_name")
    ent = find_by_ref(scene_id, ref) if ref else None

    if ent is None:
        if not delta.get("register_kind"):
            return None
        name = delta.get("register_name") or delta.get("entity_ref")
        if not name:
            return None
        if delta.get("register_kind") == "location":
            loc = register_location(
                name,
                aliases=delta.get("aliases", []),
                notes=delta.get("note", ""),
                coordinate_anchor_id=scene_id,
            )
            return loc["id"]
        ent_id = f"ent_{uuid.uuid4().hex[:10]}"
        upsert_entity(
            id=ent_id, scene_id=scene_id, kind=delta["register_kind"], name=name,
            aliases=delta.get("aliases", []), status=delta.get("status", "present"),
            disposition=delta.get("disposition"), notes=delta.get("note", ""),
        )
        if delta.get("commitment"):
            append_entity_commitment(ent_id, delta["commitment"])
        return ent_id

    # Merge updates onto the existing entity.
    status = delta.get("status")
    disposition = delta.get("disposition")
    note = delta.get("note")
    notes = ent["notes"]
    if note:
        notes = _cap_notes((notes + "\n" + note).strip() if notes else note)
    # Location writes are validated against the global registry: the extractor may move an
    # entity to a KNOWN place (presence follows it — see move_entity) but cannot strand it
    # at a hallucinated location_id. It can never move the party — party_location_id is
    # engine-only (see GameState invariant).
    new_location_id = ent["location_id"]
    scene_for_upsert = ent["scene_id"]
    requested_loc = delta.get("location_id")
    if requested_loc:
        loc = get_entity_by_id(requested_loc) or find_location(requested_loc)
        if loc is not None and loc.get("kind") == "location":
            new_location_id = loc["id"]
            scene_for_upsert = loc["id"]  # relocate: presence is scoped by scene_id
        else:
            log.info("apply_delta: dropped unknown location_id=%r for entity %s",
                     requested_loc, ent["id"])
    upsert_entity(
        id=ent["id"], scene_id=scene_for_upsert, kind=ent["kind"], name=ent["name"],
        aliases=ent.get("aliases", []),
        status=status if status in ENTITY_STATUSES else ent["status"],
        location_id=new_location_id,
        disposition=disposition if disposition in DISPOSITIONS else ent["disposition"],
        flags=ent.get("flags", {}), notes=notes,
        first_seen_event_id=ent.get("first_seen_event_id"),
    )
    # Mechanical condition deltas. Done after the upsert so the latest flags blob
    # is what add/remove read; unknown ids were already filtered by the schema
    # validator, but we still gate on str/non-empty here for direct dict callers.
    for cid in delta.get("add_conditions") or []:
        if isinstance(cid, str) and cid:
            add_condition(ent["id"], cid)
    for cid in delta.get("remove_conditions") or []:
        if isinstance(cid, str) and cid:
            remove_condition(ent["id"], cid)
    # Durable promise/standing fact this NPC made → flags so it is re-injected every
    # turn (like agenda) and never scrolls out of the event window.
    if delta.get("commitment"):
        append_entity_commitment(ent["id"], delta["commitment"])
    return ent["id"]


# ───────────────────────── location persistent state ─────────────────────────
def append_location_state_note(location_id: str, note: str) -> bool:
    """Record a lasting change to a PLACE itself (not tied to a person/object) — e.g.
    'the tripwire is disarmed', 'a waterskin spilled here'. Stored on the location
    entity's flags so it survives revisits and is folded into the dynamic summary.
    Capped like entity notes. Returns True if written."""
    if not location_id or not note or not note.strip():
        return False
    loc = get_entity_by_id(location_id)
    if not loc or loc.get("kind") != "location":
        return False
    flags = dict(loc.get("flags") or {})
    existing = flags.get("state_notes") or ""
    flags["state_notes"] = _cap_notes((existing + "\n" + note).strip() if existing else note.strip())
    upsert_entity(
        id=loc["id"], scene_id=loc["scene_id"], kind=loc["kind"], name=loc["name"],
        aliases=loc.get("aliases", []), status=loc["status"],
        location_id=loc.get("location_id"), disposition=loc.get("disposition"),
        flags=flags, notes=loc.get("notes", ""),
        first_seen_event_id=loc.get("first_seen_event_id"),
    )
    return True


def location_state_note(location_id: str) -> str:
    """The accumulated persistent place-state for a location (may be empty)."""
    loc = get_entity_by_id(location_id) if location_id else None
    if not loc or loc.get("kind") != "location":
        return ""
    return ((loc.get("flags") or {}).get("state_notes") or "").strip()


# ───────────────────────── entity commitments ─────────────────────────
def append_entity_commitment(entity_id: str, text: str) -> bool:
    """Record a durable promise / standing fact / attitude shift an NPC made toward the
    party (e.g. '答應帶路去地窖'). Stored on the entity's flags so it is re-injected into
    every narration regardless of the event window. De-duped and capped. Returns True if
    written."""
    if not entity_id or not text or not text.strip():
        return False
    ent = get_entity_by_id(entity_id)
    if not ent:
        return False
    flags = dict(ent.get("flags") or {})
    existing = [c for c in (flags.get("commitments") or []) if isinstance(c, str)]
    clean = text.strip()
    if clean in existing:
        return False
    # Keep the most recent few; older promises usually fold into newer ones.
    flags["commitments"] = (existing + [clean])[-6:]
    upsert_entity(
        id=ent["id"], scene_id=ent["scene_id"], kind=ent["kind"], name=ent["name"],
        aliases=ent.get("aliases", []), status=ent["status"],
        location_id=ent.get("location_id"), disposition=ent.get("disposition"),
        flags=flags, notes=ent.get("notes", ""),
        first_seen_event_id=ent.get("first_seen_event_id"),
    )
    return True


def entity_commitments(entity: dict) -> list[str]:
    """The durable commitments recorded on an entity dict (may be empty)."""
    flags = entity.get("flags") or {}
    raw = flags.get("commitments") if isinstance(flags, dict) else None
    return [c for c in (raw or []) if isinstance(c, str)]


# ───────────────────────── entity reflections ─────────────────────────
def append_entity_reflection(entity_id: str, text: str) -> bool:
    """Record a durable NPC impression of the party/player. Stored on flags like
    commitments so it is re-injected even after the event window scrolls away."""
    if not entity_id or not text or not text.strip():
        return False
    ent = get_entity_by_id(entity_id)
    if not ent or ent.get("kind") not in {"person", "creature"}:
        return False
    flags = dict(ent.get("flags") or {})
    existing = [r for r in (flags.get("reflections") or []) if isinstance(r, str)]
    clean = text.strip()
    if clean in existing:
        return False
    flags["reflections"] = (existing + [clean])[-6:]
    upsert_entity(
        id=ent["id"], scene_id=ent["scene_id"], kind=ent["kind"], name=ent["name"],
        aliases=ent.get("aliases", []), status=ent["status"],
        location_id=ent.get("location_id"), disposition=ent.get("disposition"),
        flags=flags, notes=ent.get("notes", ""),
        first_seen_event_id=ent.get("first_seen_event_id"),
    )
    return True


def entity_reflections(entity: dict) -> list[str]:
    """The durable NPC impressions recorded on an entity dict (may be empty)."""
    flags = entity.get("flags") or {}
    raw = flags.get("reflections") if isinstance(flags, dict) else None
    return [r for r in (raw or []) if isinstance(r, str)]


# ───────────────────────── scene_state ─────────────────────────
def set_base_summary(scene_id: str, base_summary: str) -> None:
    with _lock:
        c = _c()
        row = c.execute("SELECT scene_id FROM scene_state WHERE scene_id=?", (scene_id,)).fetchone()
        if row:
            c.execute("UPDATE scene_state SET base_summary=?, updated_ts=? WHERE scene_id=?",
                      (base_summary, _now(), scene_id))
        else:
            c.execute("INSERT INTO scene_state (scene_id, base_summary, current_summary, updated_ts) "
                      "VALUES (?,?,?,?)", (scene_id, base_summary, base_summary, _now()))
        c.commit()


def set_current_summary(scene_id: str, current_summary: str) -> None:
    with _lock:
        c = _c()
        row = c.execute("SELECT scene_id FROM scene_state WHERE scene_id=?", (scene_id,)).fetchone()
        if row:
            c.execute("UPDATE scene_state SET current_summary=?, updated_ts=? WHERE scene_id=?",
                      (current_summary, _now(), scene_id))
        else:
            c.execute("INSERT INTO scene_state (scene_id, base_summary, current_summary, updated_ts) "
                      "VALUES (?,?,?,?)", (scene_id, "", current_summary, _now()))
        c.commit()


def get_scene_state(scene_id: str) -> dict | None:
    with _lock:
        c = _c()
        row = c.execute("SELECT * FROM scene_state WHERE scene_id=?", (scene_id,)).fetchone()
    return dict(row) if row else None


# ───────────────────────── location cards ─────────────────────────
_LOCATION_CARD_LIST_FIELDS = (
    "aliases",
    "sensory_anchors",
    "visual_landmarks",
    "interactive_features",
    "discoverables",
    "hazards",
    "soft_hooks",
    "exits_hint",
)


def _location_card_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for key in _LOCATION_CARD_LIST_FIELDS:
        try:
            d[key] = json.loads(d.get(key) or "[]")
        except json.JSONDecodeError:
            d[key] = []
    return d


def upsert_location_card(
    *,
    location_id: str,
    canonical_name: str,
    aliases: list[str] | None = None,
    base_summary: str = "",
    sensory_anchors: list[str] | None = None,
    visual_landmarks: list[str] | None = None,
    interactive_features: list[str] | None = None,
    discoverables: list[str] | None = None,
    hazards: list[str] | None = None,
    soft_hooks: list[str] | None = None,
    exits_hint: list[str] | None = None,
    mood: str = "",
    generated_source: str = "",
    generated_ts: float | None = None,
) -> None:
    """Store stable exploration and description anchors for one location."""
    now = _now()
    gen_ts = generated_ts or now
    lists = {
        "aliases": aliases or [],
        "sensory_anchors": sensory_anchors or [],
        "visual_landmarks": visual_landmarks or [],
        "interactive_features": interactive_features or [],
        "discoverables": discoverables or [],
        "hazards": hazards or [],
        "soft_hooks": soft_hooks or [],
        "exits_hint": exits_hint or [],
    }
    with _lock:
        c = _c()
        row = c.execute(
            "SELECT location_id FROM location_cards WHERE location_id=?", (location_id,)
        ).fetchone()
        values = (
            canonical_name,
            json.dumps(lists["aliases"], ensure_ascii=False),
            base_summary,
            json.dumps(lists["sensory_anchors"], ensure_ascii=False),
            json.dumps(lists["visual_landmarks"], ensure_ascii=False),
            json.dumps(lists["interactive_features"], ensure_ascii=False),
            json.dumps(lists["discoverables"], ensure_ascii=False),
            json.dumps(lists["hazards"], ensure_ascii=False),
            json.dumps(lists["soft_hooks"], ensure_ascii=False),
            json.dumps(lists["exits_hint"], ensure_ascii=False),
            mood,
            generated_source,
            gen_ts,
            now,
        )
        if row:
            c.execute(
                "UPDATE location_cards SET canonical_name=?, aliases=?, base_summary=?, "
                "sensory_anchors=?, visual_landmarks=?, interactive_features=?, "
                "discoverables=?, hazards=?, soft_hooks=?, exits_hint=?, mood=?, "
                "generated_source=?, generated_ts=?, updated_ts=? WHERE location_id=?",
                (*values, location_id),
            )
        else:
            c.execute(
                "INSERT INTO location_cards (location_id, canonical_name, aliases, "
                "base_summary, sensory_anchors, visual_landmarks, interactive_features, "
                "discoverables, hazards, soft_hooks, exits_hint, mood, generated_source, "
                "generated_ts, updated_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (location_id, *values),
            )
        c.commit()


def get_location_card(location_id: str) -> dict | None:
    if not location_id:
        return None
    with _lock:
        c = _c()
        row = c.execute(
            "SELECT * FROM location_cards WHERE location_id=?", (location_id,)
        ).fetchone()
    return _location_card_to_dict(row)


def locations_missing_cards() -> list[dict]:
    with _lock:
        c = _c()
        rows = c.execute(
            "SELECT e.* FROM entities e "
            "LEFT JOIN location_cards lc ON lc.location_id=e.id "
            "WHERE e.kind='location' AND lc.location_id IS NULL "
            "ORDER BY e.created_ts"
        ).fetchall()
    return [_entity_to_dict(r) for r in rows]


def clear_mention(scene_id: str | None, name: str) -> None:
    if not name or not name.strip():
        return
    with _lock:
        c = _c()
        c.execute(
            "DELETE FROM mention_tally WHERE scene_id IS ? AND norm_name=?",
            (scene_id, name.strip().lower()),
        )
        c.commit()
