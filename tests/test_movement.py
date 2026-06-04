import pytest

from app.content import scenario
from app.db import store
from app.discord_bot import bot as discord_bot
from app.engine import movement
from app.state import game_state
from app.world import movement as world_movement


def test_dex_speed_multiplier_defaults_and_bounds():
    assert movement.dex_speed_multiplier(None) == 1.0
    assert movement.dex_speed_multiplier(10) == 1.0
    assert movement.dex_speed_multiplier(1) >= 0.6
    assert movement.dex_speed_multiplier(40) <= 1.5


def test_compute_speed_and_travel_time_hours():
    speed = movement.compute_speed(5, dex=10, vehicle_mod=1.0, terrain_mod=1.0)
    assert speed == pytest.approx(5.0)
    assert movement.travel_time_hours(10, speed) == pytest.approx(2.0)


def test_vehicle_modifier_defaults_and_seeded_horse():
    assert store.vehicle_modifier("horse") == pytest.approx(2.0)
    assert store.vehicle_modifier("unknown") == pytest.approx(1.0)
    assert store.vehicle_modifier(None) == pytest.approx(1.0)


def test_connection_id_round_trip():
    cid = world_movement.connection_id("a", "b")
    assert cid == "conn:a__b"
    assert world_movement.parse_connection(cid) == ("a", "b")
    assert world_movement.parse_connection("tavern") is None


def test_start_transit_and_lazy_advance_arrives_at_destination():
    store.register_location(
        "A",
        location_id="a",
        flags={"connects": ["b"], "distances": {"b": 10}},
    )
    store.register_location(
        "B",
        location_id="b",
        flags={"connects": ["a"], "distances": {"a": 10}},
    )
    store.upsert_entity(
        id="ent_runner",
        scene_id="a",
        kind="person",
        name="Runner",
        location_id="a",
        flags={"movement_base": 5},
    )

    ent = world_movement.start_transit("ent_runner", "a", "b", 540)

    assert ent is not None
    assert ent["scene_id"] == "conn:a__b"
    assert ent["location_id"] == "conn:a__b"
    assert ent["flags"]["transit"]["time_h"] == pytest.approx(2.0)

    assert world_movement.advance_transits(659) == []
    still_moving = store.get_entity_by_id("ent_runner")
    assert still_moving["scene_id"] == "conn:a__b"

    arrived = world_movement.advance_transits(660)
    assert [e["id"] for e in arrived] == ["ent_runner"]
    saved = store.get_entity_by_id("ent_runner")
    assert saved["scene_id"] == "b"
    assert saved["location_id"] == "b"
    assert "transit" not in saved["flags"]


def test_party_travel_plan_uses_distance_and_minutes():
    gs = game_state.reset_state(channel_id=0)
    plan = discord_bot._plan_travel("tavern", "warren", gs.pcs())

    assert plan["traversed"] == ["morningbridge", "east_road", "warren"]
    assert plan["distance_km"] == pytest.approx(15.2)
    assert plan["time_h"] > 0

    start = gs.world_minutes()
    gs.advance_minutes(plan["time_h"] * 60)

    assert gs.world_minutes() == start + round(plan["time_h"] * 60)


def test_seeded_edge_distance_and_unit_speed_read_location_flags():
    store.seed_locations(scenario.LOCATIONS)

    assert world_movement.edge_distance("east_road", "warren") == pytest.approx(10.0)
    thief = {"flags": {"movement_base": 5}}
    assert world_movement.unit_speed(thief, "warren") == pytest.approx(3.5)
