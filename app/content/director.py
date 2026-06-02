"""Goal director (design §7.3): the soft story spine that replaces the rigid scene index.

It reads structured world state — flags plus the party's visited-locations trail — to
decide which story beat is active, which are done, and which were *skipped* because the
players bypassed them. It NEVER moves the party; it only tracks progress and offers an
in-world nudge when the table stalls. This is what lets extreme player choices drift the
plot without the world snapping back to a scripted scene.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import scenario

if TYPE_CHECKING:
    from ..state.game_state import GameState

# Freeplay beats without goal progress before the active beat earns an in-world nudge.
STALL_THRESHOLD = 3


def _is_done(gs: "GameState", goal: dict) -> bool:
    if any(gs.flags.get(f) for f in goal.get("done_flags", [])):
        return True
    reached = goal.get("done_if_reached", [])
    if not reached:
        return False
    if gs.current_location_id in reached:
        return True
    visited = set(gs.flags.get("visited_locations", []))
    return bool(visited & set(reached))


def evaluate(gs: "GameState") -> dict:
    """Compute goal state from the world right now: {done, skipped, active, all_done}."""
    statuses = [(g, _is_done(gs, g)) for g in scenario.GOALS]
    done = [g["id"] for g, ok in statuses if ok]
    active = next((g for g, ok in statuses if not ok), None)
    # A beat counts as 'skipped' when it is NOT done but some LATER beat IS done.
    skipped: list[str] = []
    later_done = False
    for g, ok in reversed(statuses):
        if ok:
            later_done = True
        elif later_done:
            skipped.append(g["id"])
    skipped.reverse()
    all_done = any(g.get("terminal") and ok for g, ok in statuses)
    return {"done": done, "skipped": skipped, "active": active, "all_done": all_done}


def record(gs: "GameState") -> dict:
    """Recompute goal state, persist it on flags, and report what just changed:
    {newly_done, skipped, active, all_done}. Resets the stall counter on progress."""
    ev = evaluate(gs)
    prev_done = set(gs.flags.get("goals_done", []))
    newly = [gid for gid in ev["done"] if gid not in prev_done]
    gs.flags["goals_done"] = ev["done"]
    gs.flags["goals_skipped"] = ev["skipped"]
    if newly:
        gs.flags["beats_since_progress"] = 0
    return {"newly_done": newly, "skipped": ev["skipped"],
            "active": ev["active"], "all_done": ev["all_done"]}


def note_beat(gs: "GameState") -> int:
    """Count a freeplay beat that made no goal progress; returns the running stall count."""
    n = int(gs.flags.get("beats_since_progress", 0)) + 1
    gs.flags["beats_since_progress"] = n
    return n


def nudge_if_stalled(gs: "GameState") -> str | None:
    """When the party has gone STALL_THRESHOLD beats with no progress, return an in-world
    hint for the active beat (resetting the counter); otherwise None."""
    ev = evaluate(gs)
    if ev["all_done"] or ev["active"] is None:
        return None
    if int(gs.flags.get("beats_since_progress", 0)) < STALL_THRESHOLD:
        return None
    gs.flags["beats_since_progress"] = 0
    return ev["active"].get("nudge")
