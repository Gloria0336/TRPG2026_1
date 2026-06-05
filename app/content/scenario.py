"""Scenario loader — reads the active scenario's YAML data files and exposes the same
public API the rest of the app imports (TITLE / INTRO / HOW_TO_PLAY / LOCATIONS / SCENES /
GOALS / ENDINGS + scene_by_id / first_scene / next_scene / scene_index).

A-sandbox architecture (design: location-spine, de-scripted world):
  • The location graph is the spine. ``SCENES`` is a *projection* of ``LOCATIONS`` — there
    is no independent scripted scene rail. Travelling to any authored location re-enters its
    derived "scene" (so seed_entities / encounter / cost_pool still flow through the existing
    goto_scene path), but the party is never dragged along an ordered scene index.
  • ``GOALS`` is empty — the goal director (director.py) loads but is inert: no active beat,
    nothing auto-completes, the world never auto-ends.
  • ``cost_pool`` and ``encounter`` live on LOCATIONS; narrative entities live in a global
    pool (entities.yaml) anchored to a location via the ``location`` field.
  • Only the ``defeat`` ending survives (engine TPK). Win/peaceful endings were removed.

Data lives in ``app/content/scenarios/<settings.scenario>/{meta,locations,entities}.yaml``.
Validation runs at import so a broken scenario fails at startup, not mid-game.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..config import settings
from ..logging_setup import get_logger

log = get_logger("scenario")

_SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


def _load_yaml(path: Path) -> dict:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


_BASE = _SCENARIOS_DIR / settings.scenario
_meta = _load_yaml(_BASE / "meta.yaml")
_locations_doc = _load_yaml(_BASE / "locations.yaml")
_entities_doc = _load_yaml(_BASE / "entities.yaml")

# ── meta (title / framing / start / endings) ──
TITLE: str = _meta.get("title", "")
INTRO: str = _meta.get("intro", "")
HOW_TO_PLAY: str = _meta.get("how_to_play", "")
START_LOCATION: str = _meta.get("start_location") or ""
DEFAULT_COST_POOL: list[str] = list(_meta.get("default_cost_pool", []))
ENDINGS: dict[str, str] = dict(_meta.get("endings", {}))

# ── locations (the spine) ──
LOCATIONS: list[dict] = list(_locations_doc.get("locations", []))

# ── global entity pool, grouped by anchor location for O(1) scene projection ──
_ALL_ENTITIES: list[dict] = list(_entities_doc.get("entities", []))
_ENTITIES_BY_LOCATION: dict[str, list[dict]] = {}
for _ent in _ALL_ENTITIES:
    _ENTITIES_BY_LOCATION.setdefault(_ent.get("location"), []).append(_ent)


def _validate() -> None:
    """Fail fast on dangling references; coerce cost pools to valid CostType members.

    Imports are kept local: scenario is imported very early by many modules, so we avoid
    pulling engine/content siblings at module-load time. monsters → engine.types only, so
    there is no import cycle here.
    """
    from ..engine.types import CostType
    from . import monsters

    valid_costs = {c.value for c in CostType}
    valid_monsters = set(monsters._TEMPLATES)
    loc_ids = {loc["id"] for loc in LOCATIONS}

    def _clean_pool(pool: list, where: str) -> list:
        cleaned = []
        for value in pool or []:
            if value in valid_costs:
                cleaned.append(value)
            else:
                log.warning("scenario %s: invalid cost type %r in %s — dropped",
                            settings.scenario, value, where)
        return cleaned

    global DEFAULT_COST_POOL
    DEFAULT_COST_POOL = _clean_pool(DEFAULT_COST_POOL, "meta.default_cost_pool")

    for loc in LOCATIONS:
        if "cost_pool" in loc:
            loc["cost_pool"] = _clean_pool(loc["cost_pool"], f"location {loc['id']}.cost_pool")
        for entry in loc.get("encounter") or []:
            key = entry[0] if isinstance(entry, (list, tuple)) else entry
            if key not in valid_monsters:
                raise ValueError(
                    f"scenario {settings.scenario}: location {loc['id']} encounter references "
                    f"unknown monster {key!r} (not in monsters._TEMPLATES)")

    if START_LOCATION and START_LOCATION not in loc_ids:
        raise ValueError(
            f"scenario {settings.scenario}: start_location {START_LOCATION!r} is not a known location")

    for ent in _ALL_ENTITIES:
        anchor = ent.get("location")
        if anchor and anchor not in loc_ids:
            log.warning("scenario %s: entity %s anchored to unknown location %r",
                        settings.scenario, ent.get("id"), anchor)


_validate()


def _scene_from_location(loc: dict) -> dict:
    """Project a location into a backward-compatible scene dict. A "scene" has no independent
    existence here — it is just 'the current location's content' (summary / soft hooks /
    cost pool / encounter / the entities anchored to this place)."""
    card = loc.get("card") or {}
    ents = _ENTITIES_BY_LOCATION.get(loc["id"], [])
    return {
        "id": loc["id"],
        "title": loc.get("name", loc["id"]),
        "summary": card.get("base_summary") or loc.get("notes", ""),
        "npcs": [e["name"] for e in ents if e.get("kind") == "person"],
        "entities": ents,
        "challenges": {},                                   # AI DCAssessment owns DC now
        "cost_pool": list(loc.get("cost_pool") or DEFAULT_COST_POOL),
        "onboarding": list(card.get("soft_hooks") or []),
        "encounter": loc.get("encounter"),                  # encounter lives on the location
        "advance_hint": "",
    }


# SCENES is a projection of LOCATIONS — every authored location is enterable, and travelling
# to one re-enters its derived scene (seeding its entities/encounter). Order follows LOCATIONS.
SCENES: list[dict] = [_scene_from_location(loc) for loc in LOCATIONS]

# A-sandbox: no goal spine. director.py reads this and stays inert (no terminal, no auto-end).
GOALS: list[dict] = []


def scene_by_id(scene_id: str) -> dict | None:
    return next((s for s in SCENES if s["id"] == scene_id), None)


def scene_index(scene_id: str) -> int:
    for i, s in enumerate(SCENES):
        if s["id"] == scene_id:
            return i
    return -1


def first_scene() -> dict | None:
    """The starting location's scene (meta.start_location), falling back to the first one."""
    return scene_by_id(START_LOCATION) or (SCENES[0] if SCENES else None)


def next_scene(scene_id: str) -> dict | None:
    """A-sandbox: there is no linear scene progression. Always None — travel drives movement."""
    return None
