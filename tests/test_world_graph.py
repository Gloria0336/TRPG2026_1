"""Hierarchical world graph (design §6 location hierarchy + §12 mixed access gate):
seeding carries the graph flags, travel_options exposes one-hop neighbours, travel_path
routes through intermediates (no teleport) deterministically, and the access gate classifies
free / soft / hard entry."""
import json

from app.content import scenario
from app.db import store


def _seed() -> None:
    store.seed_locations(scenario.LOCATIONS)


def test_seed_carries_graph_flags():
    _seed()
    warren = store.get_entity_by_id("warren")
    assert warren["flags"]["loc_type"] == "wilds"
    assert warren["flags"]["danger"] == 3
    assert warren["flags"]["parent"] == "frontier"
    assert warren["flags"]["connects"] == ["east_road"]
    assert warren["flags"]["distances"]["east_road"] == 10
    assert warren["flags"]["terrain_modifier"] == 0.7


def test_seed_carries_local_coordinates():
    _seed()

    expected = {
        "frontier": {"x": 0, "y": 0},
        "morningbridge": {"coord_parent": "frontier", "x": 0, "y": 0},
        "tavern": {"coord_parent": "morningbridge", "x": 0, "y": 0},
        "east_road": {"coord_parent": "frontier", "x": 5, "y": 0},
        "warren": {"coord_parent": "frontier", "x": 15, "y": 0},
    }
    for loc_id, flags in expected.items():
        saved = store.get_entity_by_id(loc_id)["flags"]
        for key, value in flags.items():
            assert saved[key] == value
    assert "coord_parent" not in store.get_entity_by_id("frontier")["flags"]


def test_seed_backfills_existing_authored_location_coordinates():
    _seed()
    c = store._c()
    loc = store.get_entity_by_id("morningbridge")
    flags = dict(loc["flags"])
    for key in ("coord_parent", "x", "y"):
        flags.pop(key, None)
    c.execute(
        "UPDATE entities SET flags=? WHERE id=?",
        (json.dumps(flags, ensure_ascii=False), "morningbridge"),
    )
    c.commit()

    _seed()

    saved = store.get_entity_by_id("morningbridge")["flags"]
    assert saved["coord_parent"] == "frontier"
    assert saved["x"] == 0
    assert saved["y"] == 0


def test_travel_options_includes_parent_children_and_connects():
    _seed()
    opts = {l["id"] for l in store.travel_options("morningbridge")}
    assert "frontier" in opts      # parent (exit upward)
    assert "tavern" in opts        # child (enter)
    assert "east_road" in opts     # lateral connect
    # the tavern is a leaf venue: its only neighbour is its parent settlement
    assert {l["id"] for l in store.travel_options("tavern")} == {"morningbridge"}


def test_travel_path_routes_through_intermediates_deterministically():
    _seed()
    assert store.travel_path("tavern", "tavern") == []
    assert store.travel_path("tavern", "morningbridge") == ["morningbridge"]
    # no teleport: tavern → warren must leave through the village and the east road
    assert store.travel_path("tavern", "warren") == ["morningbridge", "east_road", "warren"]
    # unknown / disconnected destination → None (caller treats as emergent free travel)
    assert store.travel_path("tavern", "nowhere") is None


def test_access_gate_free_and_soft():
    _seed()
    warren = store.location_access("warren")
    assert warren["gate"] == "soft"          # danger 3 → warn, proceed
    assert warren["danger"] == 3
    assert store.location_access("tavern")["gate"] == "free"
    assert store.location_access("east_road")["gate"] == "free"


def test_access_gate_hard_on_required_rank():
    store.register_location("禁地", location_id="forbidden",
                            flags={"required_rank": "B", "gate_reason": "守軍封鎖。"})
    acc = store.location_access("forbidden")
    assert acc["gate"] == "hard"
    assert acc["required_rank"] == "B"
    assert "守軍" in acc["reason"]


def test_travel_cost_defaults_and_authored():
    _seed()
    assert store.location_travel_cost("tavern") == 0    # in-town hop is instant
    assert store.location_travel_cost("warren") == 1    # heading into the wilds burns a stage
    store.register_location("某處", location_id="somewhere")
    assert store.location_travel_cost("somewhere") == 1  # default when unspecified
