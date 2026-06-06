import pytest

from app.ai import orchestrator, prompts
from app.ai.schemas import LOCATION_CARD_JSON_SHAPE, LocationCard
from app.content import scenario
from app.db import store
from app.state import game_state
from app.world import location_registration


def test_location_card_round_trip_and_missing_list():
    loc = store.register_location("廢棄礦坑", location_id="mine")
    assert [l["id"] for l in store.locations_missing_cards()] == [loc["id"]]

    store.upsert_location_card(
        location_id=loc["id"],
        canonical_name="廢棄礦坑",
        aliases=["礦坑"],
        base_summary="潮濕的礦坑入口向山腹裂開。",
        sensory_anchors=["濕鐵味", "滴水聲"],
        visual_landmarks=["斷裂木梁"],
        interactive_features=["鬆動礦車"],
        discoverables=["新鮮腳印"],
        hazards=["落石痕跡"],
        soft_hooks=["深處傳來回音"],
        exits_hint=["東路"],
        mood="陰冷",
        generated_source="test",
    )

    card = store.get_location_card(loc["id"])
    assert card["canonical_name"] == "廢棄礦坑"
    assert card["sensory_anchors"] == ["濕鐵味", "滴水聲"]
    assert store.locations_missing_cards() == []

    store.reset_world()
    assert store.get_location_card(loc["id"]) is None


@pytest.mark.asyncio
async def test_register_location_with_card_falls_back_when_ai_disabled(monkeypatch):
    monkeypatch.setattr(orchestrator, "_ai_enabled", lambda: False)
    gs = game_state.reset_state(channel_id=1)

    loc, card = await location_registration.register_location_with_card(
        location_registration.LocationRegistrationRequest(
            requested_name="下方",
            source="player_travel",
            state=gs,
            player_text="順著東路往下走",
        )
    )

    assert loc["kind"] == "location"
    assert loc["name"] == "下方"
    assert card.base_summary
    assert store.get_location_card(loc["id"])["base_summary"] == card.base_summary
    assert store.get_scene_state(loc["id"])["base_summary"] == card.base_summary
    flags = loc["flags"]
    assert flags["coord_parent"] == "morningbridge"
    assert isinstance(flags["x"], (int, float))
    assert isinstance(flags["y"], (int, float))


@pytest.mark.asyncio
async def test_seed_location_cards_use_authored_card_without_ai(monkeypatch):
    # Seed cards are pre-authored in scenario.LOCATIONS — seeding must persist them
    # verbatim and never make a per-location AI round-trip (keeps /start latency flat).
    async def boom(request):
        raise AssertionError("build_location_card must not be called for seed locations")

    monkeypatch.setattr(orchestrator, "build_location_card", boom)
    gs = game_state.reset_state(channel_id=1)

    await location_registration.ensure_seed_location_cards(gs, scenario.LOCATIONS)

    for defn in scenario.LOCATIONS:
        authored = defn["card"]
        card = store.get_location_card(defn["id"])
        assert card is not None
        assert card["generated_source"] == "authored_seed:authored"
        assert card["base_summary"] == authored["base_summary"]
        assert card["sensory_anchors"] == authored["sensory_anchors"]
        assert store.get_scene_state(defn["id"])["base_summary"] == card["base_summary"]
        # Authored terrain_modifier is authoritative — the card must not drift it.
        assert (
            store.get_entity_by_id(defn["id"])["flags"]["terrain_modifier"]
            == authored["terrain_modifier"]
        )


@pytest.mark.asyncio
async def test_promote_location_mention_with_card_clears_tally(monkeypatch):
    async def fake_build_location_card(request):
        return (
            LocationCard(
                canonical_name=request["requested_name"],
                base_summary=f"{request['requested_name']} 被固定成可回訪的地點。",
            ),
            "ready",
        )

    monkeypatch.setattr(orchestrator, "build_location_card", fake_build_location_card)
    gs = game_state.reset_state(channel_id=1)
    store.record_mention(gs.current_location_id, "破舊神龕", "location")

    ent_id = await location_registration.promote_location_mention_with_card(
        gs.current_location_id, "破舊神龕", gs
    )

    assert ent_id is not None
    flags = store.get_entity_by_id(ent_id)["flags"]
    assert flags["coord_parent"] == "morningbridge"
    assert isinstance(flags["x"], (int, float))
    assert isinstance(flags["y"], (int, float))
    assert store.find_location("破舊神龕")["id"] == ent_id
    assert store.get_location_card(ent_id)["base_summary"].startswith("破舊神龕")
    assert store.record_mention(gs.current_location_id, "破舊神龕", "location") == 0


def test_scene_context_includes_location_card_anchor():
    gs = game_state.reset_state(channel_id=1)
    store.upsert_location_card(
        location_id=gs.current_location_id,
        canonical_name="鎏金酒杯酒館",
        base_summary="酒館裡有固定的木煙與啤酒氣味。",
        sensory_anchors=["木煙味", "啤酒酸味"],
        visual_landmarks=["刮痕累累的吧台"],
        generated_source="test",
    )

    ctx = prompts.scene_context(gs)

    assert "LOCATION CARD ANCHORS" in ctx
    assert "木煙味" in ctx
    assert "刮痕累累的吧台" in ctx


def test_location_coordinates_stay_out_of_ai_contexts():
    gs = game_state.reset_state(channel_id=1)
    loc = store.get_entity_by_id(gs.current_location_id)
    assert loc["flags"]["x"] == 0
    assert loc["flags"]["y"] == 0
    assert loc["flags"]["coord_parent"] == "morningbridge"

    card_ctx = prompts.location_card_context({
        "requested_name": "Hidden Shrine",
        "existing_locations": [loc],
    })
    intent_ctx = prompts.intent_context(gs, gs.pcs()[0], "看看四周")

    for ctx in (LOCATION_CARD_JSON_SHAPE, card_ctx, intent_ctx):
        assert "coord_parent" not in ctx
        assert "x:" not in ctx
        assert "y:" not in ctx
        assert "座標" not in ctx
