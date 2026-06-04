"""World-graph movement and in-transit entity state."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ..db import store
from ..engine import movement as movement_math

if TYPE_CHECKING:
    from ..engine.types import Character


DEFAULT_EDGE_DISTANCE_KM = 5.0
DEFAULT_MOVEMENT_BASE_KMH = 4.0
TRANSIT_FLAG = "transit"


def _flags(unit: object) -> dict:
    if isinstance(unit, dict):
        raw = unit.get("flags") or {}
        return dict(raw) if isinstance(raw, dict) else {}
    return {}


def _float_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def unit_movement_base(unit: "Character | dict") -> float:
    value = getattr(unit, "movement_base", None)
    if value is None and isinstance(unit, dict):
        flags = _flags(unit)
        value = flags.get("movement_base") or unit.get("movement_base")
    parsed = _float_or_none(value)
    return parsed if parsed is not None else DEFAULT_MOVEMENT_BASE_KMH


def unit_dex(unit: "Character | dict") -> int | None:
    abilities = getattr(unit, "abilities", None)
    if isinstance(abilities, dict):
        dex = _int_or_none(abilities.get("DEX") or abilities.get("dex"))
        if dex is not None:
            return dex
    if isinstance(unit, dict):
        flags = _flags(unit)
        abilities = flags.get("abilities")
        if isinstance(abilities, dict):
            dex = _int_or_none(abilities.get("DEX") or abilities.get("dex"))
            if dex is not None:
                return dex
        return _int_or_none(flags.get("DEX") or flags.get("dex"))
    return None


def unit_vehicle_type(unit: "Character | dict") -> str | None:
    if bool(getattr(unit, "is_vehicle", False)):
        vehicle_type = getattr(unit, "vehicle_type", None)
        return str(vehicle_type) if vehicle_type else None
    if isinstance(unit, dict):
        flags = _flags(unit)
        if bool(flags.get("is_vehicle")):
            vehicle_type = flags.get("vehicle_type")
            return str(vehicle_type) if vehicle_type else None
    return None


def _terrain_modifier(dest_id: str | None) -> float:
    dest = store.get_entity_by_id(dest_id or "") if dest_id else None
    flags = (dest or {}).get("flags") or {}
    parsed = _float_or_none(flags.get("terrain_modifier"))
    return parsed if parsed is not None else 1.0


def unit_speed(unit: "Character | dict", dest_id: str | None = None) -> float:
    vehicle_type = unit_vehicle_type(unit)
    vehicle_mod = store.vehicle_modifier(vehicle_type) if vehicle_type else 1.0
    return movement_math.compute_speed(
        unit_movement_base(unit),
        unit_dex(unit),
        vehicle_mod,
        _terrain_modifier(dest_id),
    )


def connection_id(a: str, b: str) -> str:
    return f"conn:{a}__{b}"


def parse_connection(cid: str) -> tuple[str, str] | None:
    if not cid or not cid.startswith("conn:"):
        return None
    rest = cid[5:]
    if "__" not in rest:
        return None
    a, b = rest.split("__", 1)
    return (a, b) if a and b else None


def edge_distance(a: str, b: str) -> float:
    for src, dst in ((a, b), (b, a)):
        loc = store.get_entity_by_id(src)
        flags = (loc or {}).get("flags") or {}
        distances = flags.get("distances")
        if isinstance(distances, dict):
            parsed = _float_or_none(distances.get(dst))
            if parsed is not None and parsed >= 0:
                return parsed
    return DEFAULT_EDGE_DISTANCE_KM


def start_transit(
    entity_id: str,
    from_id: str,
    to_id: str,
    now_minutes: int | float,
) -> dict | None:
    ent = store.get_entity_by_id(entity_id)
    src = store.get_entity_by_id(from_id)
    dest = store.get_entity_by_id(to_id)
    if not ent or not src or not dest or dest.get("kind") != "location":
        return None
    distance_km = edge_distance(from_id, to_id)
    speed = unit_speed(ent, to_id)
    time_h = movement_math.travel_time_hours(distance_km, speed)
    depart = int(round(float(now_minutes)))
    arrival = depart + int(math.ceil(time_h * 60))
    conn_id = connection_id(from_id, to_id)
    flags = dict(ent.get("flags") or {})
    flags[TRANSIT_FLAG] = {
        "from": from_id,
        "to": to_id,
        "distance_km": distance_km,
        "speed_kmh": speed,
        "time_h": time_h,
        "depart_minutes": depart,
        "arrival_minutes": arrival,
    }
    store.upsert_entity(
        id=ent["id"],
        scene_id=conn_id,
        kind=ent["kind"],
        name=ent["name"],
        aliases=ent.get("aliases", []),
        status=ent["status"],
        location_id=conn_id,
        disposition=ent.get("disposition"),
        flags=flags,
        notes=ent.get("notes", ""),
        first_seen_event_id=ent.get("first_seen_event_id"),
    )
    return store.get_entity_by_id(entity_id)


def advance_transits(now_minutes: int | float) -> list[dict]:
    arrived: list[dict] = []
    now = float(now_minutes)
    for ent in store.transiting_entities():
        flags = dict(ent.get("flags") or {})
        transit = flags.get(TRANSIT_FLAG)
        if not isinstance(transit, dict):
            continue
        try:
            arrival = float(transit.get("arrival_minutes"))
        except (TypeError, ValueError):
            continue
        if arrival > now:
            continue
        dest_id = str(transit.get("to") or "")
        dest = store.get_entity_by_id(dest_id)
        if not dest or dest.get("kind") != "location":
            continue
        if store.move_entity(ent["id"], dest_id):
            saved = store.get_entity_by_id(ent["id"])
            if saved:
                arrived.append(saved)
    return arrived


def _location_name(location_id: str | None) -> str:
    loc = store.get_entity_by_id(location_id or "") if location_id else None
    return (loc or {}).get("name") or (location_id or "")


def _fmt(value: float) -> str:
    text = f"{value:.1f}"
    return text.rstrip("0").rstrip(".")


def position_label(entity: dict) -> str:
    flags = dict(entity.get("flags") or {})
    transit = flags.get(TRANSIT_FLAG)
    if isinstance(transit, dict):
        src = _location_name(str(transit.get("from") or ""))
        dest = _location_name(str(transit.get("to") or ""))
        distance = _float_or_none(transit.get("distance_km")) or 0.0
        time_h = _float_or_none(transit.get("time_h")) or 0.0
        return f"在 {src} 前往 {dest} 的路上（約 {_fmt(distance)} km、{_fmt(time_h)} 小時）"
    loc_id = entity.get("location_id") or entity.get("scene_id")
    return _location_name(str(loc_id)) if loc_id else ""
