"""Pure movement math for world travel."""
from __future__ import annotations

from .types import ability_modifier


def _float_or_default(value: object, default: float) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def dex_speed_multiplier(dex: int | None) -> float:
    """Convert a DEX score into a bounded travel-speed multiplier."""
    if dex is None:
        return 1.0
    try:
        score = int(dex)
    except (TypeError, ValueError):
        return 1.0
    mult = 1.0 + 0.05 * ability_modifier(score)
    return max(0.6, min(1.5, mult))


def compute_speed(
    movement_base: float | int | None,
    dex: int | None = None,
    vehicle_mod: float | int | None = 1.0,
    terrain_mod: float | int | None = 1.0,
) -> float:
    """Travel speed in km/h, with a small floor to avoid zero-time division."""
    base = _float_or_default(movement_base, 4.0)
    vehicle = _float_or_default(vehicle_mod, 1.0)
    terrain = _float_or_default(terrain_mod, 1.0)
    return max(0.1, base * dex_speed_multiplier(dex) * vehicle * terrain)


def travel_time_hours(distance_km: float | int | None, speed: float | int | None) -> float:
    """Hours required to cross a distance at a given speed."""
    distance = max(0.0, _float_or_default(distance_km, 0.0))
    kmh = max(0.1, _float_or_default(speed, 0.1))
    return distance / kmh
