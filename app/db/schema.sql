-- AI Living World — SQLite memory layer (design §6, pragmatic subset).
-- This is the durable continuity store: event history, narrative entities with
-- state markers, and the dynamic per-scene summary. The live game mechanics
-- (HP/turns/combat) still live in GameState; this DB is the "world remembers".
--
-- A later pass can split `entities` into the design's actors/locations/items and
-- migrate to Postgres + pgvector; ids carry a kind prefix to keep that clean.

-- ── Narrative entity registry (people / objects / locations) with state markers.
--    New design (not in the spec): every entity that appears in the fiction is
--    recorded here so "who/what is present" is structured truth, not prose.
CREATE TABLE IF NOT EXISTS entities (
    id                  TEXT PRIMARY KEY,            -- ent_hooded, ent_perrin, obj_tripwire …
    scene_id            TEXT,                        -- scene it first appeared / belongs to
    kind                TEXT NOT NULL,               -- person | object | location | creature
    name                TEXT NOT NULL,
    aliases             TEXT NOT NULL DEFAULT '[]',  -- JSON array of names the narrator may use
    status              TEXT NOT NULL DEFAULT 'present',  -- present|departed|hidden|dead|destroyed|unknown
    location_id         TEXT,                        -- where it is now (scene id or another entity)
    disposition         TEXT,                        -- friendly|neutral|wary|afraid|hostile|cowed | NULL
    flags               TEXT NOT NULL DEFAULT '{}',  -- JSON: arbitrary markers, e.g. {"questioned": true}
    notes               TEXT NOT NULL DEFAULT '',    -- accumulated key facts about this entity
    first_seen_event_id TEXT,
    created_ts          REAL NOT NULL,
    updated_ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_scene ON entities(scene_id);

-- ── Dynamic scene summary. New design: base_summary is the static backstory used
--    only when opening the scene; current_summary is recomputed every beat from
--    live entity state + recent prose so it never contradicts what happened.
CREATE TABLE IF NOT EXISTS scene_state (
    scene_id        TEXT PRIMARY KEY,
    base_summary    TEXT NOT NULL DEFAULT '',
    current_summary TEXT NOT NULL DEFAULT '',
    updated_ts      REAL NOT NULL
);

-- ── Append-only history (design §5.2 / §6). Single source of "world remembers".
--    Mirrors the in-memory Event and additionally persists the AI prose.
CREATE TABLE IF NOT EXISTS event_log (
    id          TEXT PRIMARY KEY,
    scene_id    TEXT,
    actor_id    TEXT,
    actor_name  TEXT,
    kind        TEXT,
    summary     TEXT NOT NULL DEFAULT '',
    narration   TEXT NOT NULL DEFAULT '',
    scope       TEXT NOT NULL DEFAULT 'local',
    data        TEXT NOT NULL DEFAULT '{}',  -- JSON (ResolutionResult.to_dict etc.)
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_event_scene_ts ON event_log(scene_id, ts);

-- Quest board. AI GM emits a small quest seed; the quest agent later expands it
-- into stable details so NPCs do not drift between conversations.
CREATE TABLE IF NOT EXISTS quests (
    id              TEXT PRIMARY KEY,
    dedupe_key      TEXT NOT NULL UNIQUE,
    source_event_id TEXT,
    scene_id        TEXT,
    giver           TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'available',
    visibility      TEXT NOT NULL DEFAULT 'summary',
    seed            TEXT NOT NULL DEFAULT '{}',
    details         TEXT NOT NULL DEFAULT '{}',
    tags            TEXT NOT NULL DEFAULT '{}',
    detail_state    TEXT NOT NULL DEFAULT 'pending_agent',
    created_ts      REAL NOT NULL,
    updated_ts      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quests_scene_status ON quests(scene_id, status);

-- ── Mention tally: debounce for auto-registering NEW entities the AI names in prose.
--    A brand-new place/person is counted here per location scope; only after it is
--    mentioned `mention_promote_threshold` times is it promoted into `entities`. This
--    stops one-off background flavour from bloating the world, while letting genuinely
--    recurring elements persist (a player's explicit travel target bypasses this).
CREATE TABLE IF NOT EXISTS mention_tally (
    scene_id     TEXT,                       -- location scope (the party's current_location_id)
    norm_name    TEXT NOT NULL,              -- normalized (trim+lower) dedup key
    display_name TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'person',
    count        INTEGER NOT NULL DEFAULT 0,
    last_ts      REAL NOT NULL,
    PRIMARY KEY (scene_id, norm_name)
);

-- ── RAG memory (design §5.3 / §8.0). Reserved for the next phase: created here so
--    the schema is complete, but no code reads/writes it yet (embeddings deferred).
CREATE TABLE IF NOT EXISTS memory_chunks (
    id          TEXT PRIMARY KEY,
    scene_id    TEXT,
    entity_id   TEXT,
    text        TEXT NOT NULL DEFAULT '',
    embedding   BLOB,
    scope       TEXT NOT NULL DEFAULT 'local',
    ts          REAL NOT NULL
);
