# Dawnbridge Scenario Pack

`dawnbridge` is the current authored starter scenario. It is intentionally organized as a
location-first sandbox:

- `meta.yaml`: global campaign framing and start location.
- `locations.yaml`: the world graph and all location narration cards.
- `entities.yaml`: people, creatures, and objects anchored to locations.

## Current Graph

- `frontier`: region root for the surrounding borderland.
- `morningbridge`: settlement inside `frontier`.
- `tavern`: venue inside `morningbridge`; current `start_location`.
- `east_road`: wild road connected to `morningbridge` and `warren`.
- `warren`: dangerous wild/dungeon location connected to `east_road`; holds the authored
  encounter roster.

## Authored Entities

- `tavern`: `ent_perrin`, `ent_hooded`
- `east_road`: `obj_wagons`, `obj_tracks`, `obj_tripwire`
- `warren`: `ent_grix`, `ent_hostage`

## Notes For Maintenance

- Keep location ids stable. They are persisted into saves, DB rows, quests, and event logs.
- Add new places to `locations.yaml`, not to `scenario.py`.
- Add new durable NPCs/objects to `entities.yaml` and set their `location`.
- Add location-specific failure costs with `cost_pool`; otherwise `meta.default_cost_pool`
  is used.
- Add encounter monsters through `location.encounter`, using keys from
  `app/content/monsters.py`.
- The old linear scene spine has been removed. `GOALS` stays empty and travel drives play.
