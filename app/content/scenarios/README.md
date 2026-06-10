# Scenario Packs

Scenario packs are authored world data selected by `SCENARIO=<pack_id>` in `.env`.
The loader in `app/content/scenario.py` expects exactly these files under
`app/content/scenarios/<pack_id>/`:

- `meta.yaml`: campaign title, player-facing intro/help text, start location, default costs, endings.
- `locations.yaml`: the authored location graph. Locations are the spine of the world.
- `entities.yaml`: durable people, creatures, and objects anchored to locations.

Use `_template/` as the copy source for a new setting. Keep `dawnbridge/` as the current
working example.

## Runtime Flow

1. `settings.scenario` chooses the folder.
2. `scenario.py` loads the three YAML files at import time.
3. `LOCATIONS` are seeded into the DB as location entities when a new campaign starts.
4. Each location is projected into a backward-compatible `Scene`.
5. Entities whose `location` matches the current scene id are seeded when that location is entered.

Because scenes are derived from locations, do not create a separate scene list. Travel is
driven by location ids, aliases, `parent`, and `connects`.

## Authoring Contract

### `meta.yaml`

Required fields:

- `schema_version`: currently `1`.
- `id`: folder-stable scenario id.
- `title`: shown by Discord embeds and new campaign metadata.
- `intro`: player-facing campaign intro.
- `how_to_play`: player-facing help text.
- `start_location`: must match one `locations[].id`.
- `default_cost_pool`: fallback cost pool for locations without their own `cost_pool`.
- `endings.defeat`: used by the engine when the party is defeated.

Optional ending keys such as `victory` or `peaceful` are supported by some older code paths,
but the current sandbox contract leaves `GOALS` empty and does not require them.

Allowed `default_cost_pool` / `cost_pool` values:

- `time`
- `exposure`
- `resource`
- `trace`
- `attention`
- `relation`
- `debt`

### `locations.yaml`

Required fields per location:

- `id`: stable ASCII identifier. This is referenced by `start_location`, `parent`,
  `connects`, `distances`, entity `location`, saves, tests, and event history.
- `name`: player-facing Traditional Chinese display name.

Common graph fields:

- `aliases`: names players may type.
- `loc_type`: suggested values are `region`, `settlement`, `venue`, `wilds`, `dungeon`,
  `road`, or another setting-specific label.
- `parent`: containment hierarchy. A tavern can be inside a town; a town inside a region.
- `connects`: direct travel edges to other location ids.
- `distances`: optional per-neighbor distance map, used by movement helpers.
- `x`, `y`, `coord_parent`: optional coordinate data for deterministic distance fallback.
- `travel_cost`: stage cost for entering the location.
- `danger`: integer risk signal; `3+` is a soft warning by the access gate.
- `required_rank`: hard gate marker for future progression rules.
- `gate`: `free`, `soft`, or `hard`.
- `gate_reason`: player-facing reason for a gate.
- `terrain_modifier`: travel friction; `1.0` is normal, lower is rougher.
- `cost_pool`: location-specific failure-cost pool.
- `encounter`: optional combat roster using monster template keys from `app/content/monsters.py`.

`card` is the reusable narration anchor. The location-registration and narration prompts
read these fields when present:

- `base_summary`
- `sensory_anchors`
- `visual_landmarks`
- `interactive_features`
- `discoverables`
- `hazards`
- `soft_hooks`
- `exits_hint`
- `mood`
- `terrain_modifier`

### `entities.yaml`

Required fields per entity:

- `id`: stable ASCII identifier. Do not rename once a campaign may have saved state.
- `location`: location id where the entity starts.
- `kind`: `person`, `creature`, `object`, or `location`.
- `name`: player-facing display name.

Common fields:

- `aliases`: references the parser can resolve.
- `status`: usually `present`; absent statuses include `departed`, `dead`, `destroyed`.
- `disposition`: one of `friendly`, `neutral`, `wary`, `afraid`, `hostile`, `attack`, `cowed`.
- `notes`: durable GM truth for narration and parsing.
- `flags`: free structured metadata. Existing uses include `agenda`, `movement_base`,
  vehicle flags, and condition metadata.
- `statblock`: optional note for authors. Combat spawning currently reads location
  `encounter` keys or entity-backed archetypes, so this is documentation unless code is
  extended.

## Swapping Worlds

For a new world, copy `_template/` to a new folder, edit the three YAML files, then set:

```env
SCENARIO=<new_folder_name>
```

Start a fresh campaign or clear old runtime state. Existing `save/session.json` and
campaign DBs contain old characters, location ids, entities, and event history.
