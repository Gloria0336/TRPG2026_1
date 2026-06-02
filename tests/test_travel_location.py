"""Step 1 — location as first-class state + parser/narrator same-source.

These lock the fix for the C4-mine drift: natural-language travel now writes the
party's location back to structured state, and the intent parser reads the dynamic
scene summary (compose_scene_summary) instead of the static scripted blurb — so the
two layers can no longer diverge and snap the fiction back to the tavern.
"""
from app.ai import prompts
from app.db import store
from app.state import game_state


def test_resolve_or_register_location_creates_then_reuses():
    # First reference to an unknown place creates a location entity (threshold 1).
    loc = store.resolve_or_register_location("廢棄礦坑")
    assert loc is not None and loc["kind"] == "location" and loc["name"] == "廢棄礦坑"
    # A second reference to the same place reuses it — no duplicate.
    again = store.resolve_or_register_location("廢棄礦坑")
    assert again["id"] == loc["id"]


def test_resolve_empty_ref_returns_none():
    assert store.resolve_or_register_location("") is None
    assert store.resolve_or_register_location("   ") is None


def test_resolve_does_not_hijack_a_person_entity():
    store.seed_entities("tavern", [
        {"id": "ent_perrin", "kind": "person", "name": "老佩林", "aliases": ["佩林"]},
    ])
    # The location registry only matches kind='location', so a place sharing a name with
    # an NPC still makes a distinct location, never the person.
    loc = store.resolve_or_register_location("老佩林")
    assert loc["kind"] == "location" and loc["id"] != "ent_perrin"


def test_goto_location_switches_party_scope():
    gs = game_state.reset_state(channel_id=0)
    assert gs.current_location_id == "tavern"
    assert "老佩林" in {e["name"] for e in gs.present_entities()}

    loc = store.resolve_or_register_location("廢棄礦坑")
    gs.goto_location(loc["id"], title=loc["name"], summary="黑洞洞的礦坑入口。")

    assert gs.current_location_id == loc["id"]
    assert gs.scene.id == loc["id"]
    assert gs.scene.title == "廢棄礦坑"
    # Tavern NPCs are scoped to the tavern and must NOT bleed into the new location.
    assert gs.present_entities() == [] or "老佩林" not in {e["name"] for e in gs.present_entities()}


def test_intent_context_follows_party_to_new_location():
    gs = game_state.reset_state(channel_id=0)
    actor = gs.pcs()[0]

    ctx_tavern = prompts.intent_context(gs, actor, "前往廢棄礦坑")
    assert "鎏金酒杯酒館" in ctx_tavern  # starts grounded in the tavern

    loc = store.resolve_or_register_location("廢棄礦坑")
    gs.goto_location(loc["id"], title=loc["name"], summary="黑洞洞的礦坑入口。")

    ctx_mine = prompts.intent_context(gs, actor, "拿出炸藥")
    # The parser is now grounded at the mine, NOT the stale tavern blurb.
    assert "廢棄礦坑" in ctx_mine
    assert "鎏金酒杯酒館" not in ctx_mine


def test_party_location_id_survives_snapshot_roundtrip():
    gs = game_state.reset_state(channel_id=0)
    loc = store.resolve_or_register_location("廢棄礦坑")
    gs.goto_location(loc["id"], title=loc["name"], summary="入口。")

    restored = game_state.GameState.from_dict(gs.to_dict())
    assert restored.party_location_id == loc["id"]
    assert restored.current_location_id == loc["id"]


# ── Step 2: authored locations seeded globally, canonical resolution ──

def test_seeded_locations_resolve_to_canonical_ids():
    game_state.reset_state(channel_id=0)  # seeds scenario.LOCATIONS
    # Names and aliases both resolve to the authored ids — no duplicates created.
    assert store.resolve_or_register_location("東路")["id"] == "east_road"
    assert store.resolve_or_register_location("東邊道路")["id"] == "east_road"
    assert store.resolve_or_register_location("巢穴")["id"] == "warren"  # alias of the warren
    assert store.resolve_or_register_location("酒館")["id"] == "tavern"


def test_travel_to_authored_place_keeps_scene_content():
    gs = game_state.reset_state(channel_id=0)
    loc = store.resolve_or_register_location("東路")
    assert loc["id"] == "east_road"
    # The bot bridges authored places back to goto_scene; assert that path restores the
    # authored challenges (which a bare goto_location would not have).
    from app.content import scenario
    gs.goto_scene(scenario.scene_by_id(loc["id"]))
    assert gs.current_location_id == "east_road"
    assert "investigation" in gs.scene.challenges
