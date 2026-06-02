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
from ..logging_setup import get_logger

log = get_logger("db")

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# Canonical state vocabularies. Kept here (data layer) so the schema/extractor share
# one source of truth; app/ai/schemas.py imports these for the validated LLM output.
ENTITY_KINDS = ("person", "object", "location", "creature")
ENTITY_STATUSES = ("present", "departed", "hidden", "dead", "destroyed", "unknown")
DISPOSITIONS = ("friendly", "neutral", "wary", "afraid", "hostile", "cowed")

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
        _conn.commit()
        _conn_path = target
        log.info("db: initialized at %s", target)


def _c() -> sqlite3.Connection:
    if _conn is None or _conn_path != str(settings.db_path):
        init_db()
    assert _conn is not None
    return _conn


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
        c.execute("DELETE FROM entities")
        c.execute("DELETE FROM scene_state")
        c.execute("DELETE FROM event_log")
        c.execute("DELETE FROM memory_chunks")
        c.execute("DELETE FROM mention_tally")
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


def register_location(name: str, *, location_id: str | None = None,
                      aliases: list[str] | None = None, notes: str = "") -> dict:
    """Create (or refresh) a global location entity and return it."""
    ent_id = location_id or f"loc_{uuid.uuid4().hex[:10]}"
    upsert_entity(
        id=ent_id, scene_id=LOCATION_SCOPE, kind="location", name=name.strip(),
        aliases=aliases or [], status="present", notes=notes,
    )
    return get_entity_by_id(ent_id)  # type: ignore[return-value]


def seed_locations(defs: list[dict]) -> None:
    """Register a campaign's authored locations once (id-stable, insert-only) so travel
    resolves to canonical ids instead of forking ad-hoc duplicates."""
    for d in defs:
        if d.get("id") and get_entity_by_id(d["id"]):
            continue
        register_location(
            d["name"], location_id=d.get("id"),
            aliases=d.get("aliases", []), notes=d.get("notes", ""),
        )


def resolve_or_register_location(ref: str) -> dict | None:
    """Travel helper (design: location is first-class state). Resolve a free-text place
    reference against the global registry; if none exists, create one immediately — a
    player's explicit travel target is authoritative (threshold 1, vs the AI-mention
    threshold in record_mention). Returns the location entity dict, or None for empty ref."""
    if not ref or not ref.strip():
        return None
    return find_location(ref) or register_location(ref.strip())


# ───────────────────────── mention tally (debounced auto-register) ─────────────────────────
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
        ent_id = f"ent_{uuid.uuid4().hex[:10]}"
        upsert_entity(
            id=ent_id, scene_id=scene_id, kind=delta["register_kind"], name=name,
            aliases=delta.get("aliases", []), status=delta.get("status", "present"),
            disposition=delta.get("disposition"), notes=delta.get("note", ""),
        )
        return ent_id

    # Merge updates onto the existing entity.
    status = delta.get("status")
    disposition = delta.get("disposition")
    note = delta.get("note")
    notes = ent["notes"]
    if note:
        notes = (notes + "\n" + note).strip() if notes else note
    upsert_entity(
        id=ent["id"], scene_id=ent["scene_id"], kind=ent["kind"], name=ent["name"],
        aliases=ent.get("aliases", []),
        status=status if status in ENTITY_STATUSES else ent["status"],
        location_id=delta.get("location_id", ent["location_id"]),
        disposition=disposition if disposition in DISPOSITIONS else ent["disposition"],
        flags=ent.get("flags", {}), notes=notes,
        first_seen_event_id=ent.get("first_seen_event_id"),
    )
    return ent["id"]


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
