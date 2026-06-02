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
    assert "LOCATION: 鎏金酒杯酒館" in ctx_tavern  # starts grounded in the tavern

    loc = store.resolve_or_register_location("廢棄礦坑")
    gs.goto_location(loc["id"], title=loc["name"], summary="黑洞洞的礦坑入口。")

    ctx_mine = prompts.intent_context(gs, actor, "拿出炸藥")
    # The parser is now grounded at the mine, NOT the stale tavern blurb. The
    # tavern may legitimately appear in the EXITS list (it's now a place you
    # can travel back to), but it must not be presented as the current LOCATION.
    assert "LOCATION: 廢棄礦坑" in ctx_mine
    assert "LOCATION: 鎏金酒杯酒館" not in ctx_mine


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


# ── Step 3: prompts read as LOCATION-driven, not SCENE-driven ──

def test_intent_prompt_uses_location_label_not_scene_title():
    gs = game_state.reset_state(channel_id=0)
    actor = gs.pcs()[0]
    ctx = prompts.intent_context(gs, actor, "看看四周")
    assert "LOCATION:" in ctx
    assert "SCENE:" not in ctx                       # the SCENE: header is gone
    assert "鎏金酒杯酒館" in ctx                       # clean location name shown
    assert "場景 1：" not in ctx                       # the scripted scene title is suppressed


def test_intent_prompt_lists_known_exits():
    gs = game_state.reset_state(channel_id=0)
    actor = gs.pcs()[0]
    ctx = prompts.intent_context(gs, actor, "出發")
    assert "EXITS" in ctx
    assert "東路" in ctx          # authored neighbour
    assert "哥布林巢穴" in ctx     # another authored neighbour
    # The current location must NOT appear in EXITS — only places you can travel TO.
    exit_section = ctx.split("EXITS")[1]
    # Tavern alias still appears in LOCATION line above; only check the EXITS region
    # which ends at "Known location checks".
    exit_block = exit_section.split("Known location checks")[0]
    assert "鎏金酒杯酒館" not in exit_block


def test_narrate_prompt_also_uses_location_label():
    from app.engine.types import ResolutionResult, ResultKind
    gs = game_state.reset_state(channel_id=0)
    result = ResolutionResult(
        kind=ResultKind.NARRATIVE, actor_id="pc_bram", actor_name="Bram Ironwood",
        summary="walk to the window",
    )
    ctx = prompts.narrate_context(gs, result)
    assert ctx.startswith("LOCATION:") or "\nLOCATION:" in ctx
    assert "場景 1：" not in ctx


def test_known_exits_excludes_current_location():
    gs = game_state.reset_state(channel_id=0)
    exits = prompts.known_exits(gs)
    ids = [e["id"] for e in exits]
    assert "tavern" not in ids
    assert "east_road" in ids


# ── Step 4: dispatch detects travel from any movement verb ──

def test_looks_like_travel_recognises_chinese_move_verb_with_known_location():
    from app.discord_bot.bot import _looks_like_travel
    from app.engine.types import Intent, IntentTier

    gs = game_state.reset_state(channel_id=0)
    intent = Intent(actor_id="pc_bram", raw_text="前往東路", tier=IntentTier.A,
                    action="前往", target="東路")
    assert _looks_like_travel(gs, intent) is True


def test_looks_like_travel_recognises_english_move_verb_with_alias():
    from app.discord_bot.bot import _looks_like_travel
    from app.engine.types import Intent, IntentTier

    gs = game_state.reset_state(channel_id=0)
    # "東邊道路" is an alias of east_road in the authored registry.
    intent = Intent(actor_id="pc_bram", raw_text="head east", tier=IntentTier.A,
                    action="head", target="東邊道路")
    assert _looks_like_travel(gs, intent) is True


def test_looks_like_travel_recognises_leave_current_location():
    """The trace.log bug: '走出酒館' was parsed as action=walk target=酒館 — same
    name as the current location — and previously fell through to a narrative beat
    that didn't actually move the party."""
    from app.discord_bot.bot import _looks_like_travel
    from app.engine.types import Intent, IntentTier

    gs = game_state.reset_state(channel_id=0)
    intent = Intent(actor_id="pc_bram", raw_text="走出酒館", tier=IntentTier.A,
                    action="walk", target="酒館")
    assert _looks_like_travel(gs, intent) is True


def test_looks_like_travel_ignores_follow_a_person():
    """'follow' is a travel verb but its target here is an NPC, not a location —
    the party should not teleport because someone said 'I follow the merchant'."""
    from app.discord_bot.bot import _looks_like_travel
    from app.engine.types import Intent, IntentTier

    gs = game_state.reset_state(channel_id=0)
    intent = Intent(actor_id="pc_bram", raw_text="跟隨商人", tier=IntentTier.A,
                    action="follow", target="老佩林")
    assert _looks_like_travel(gs, intent) is False


def test_looks_like_travel_ignores_non_travel_verb():
    from app.discord_bot.bot import _looks_like_travel
    from app.engine.types import Intent, IntentTier

    gs = game_state.reset_state(channel_id=0)
    intent = Intent(actor_id="pc_bram", raw_text="詢問商人東路情況", tier=IntentTier.A,
                    action="persuade", approach="persuasion", target="東路")
    assert _looks_like_travel(gs, intent) is False


# ── Step 5: leave-current redirects to an outside, never loops ──

def test_resolve_travel_target_leaves_current_to_another_known_location():
    from app.discord_bot.bot import _resolve_travel_target

    gs = game_state.reset_state(channel_id=0)
    # In the tavern; ask to leave it. Resolver must return something other than 'tavern'.
    loc = _resolve_travel_target(gs, "酒館")
    assert loc is not None
    assert loc["id"] != "tavern"


def test_resolve_travel_target_to_known_location_returns_it():
    from app.discord_bot.bot import _resolve_travel_target

    gs = game_state.reset_state(channel_id=0)
    loc = _resolve_travel_target(gs, "東路")
    assert loc is not None and loc["id"] == "east_road"


def test_resolve_travel_target_emergent_place_auto_registers():
    from app.discord_bot.bot import _resolve_travel_target

    gs = game_state.reset_state(channel_id=0)
    loc = _resolve_travel_target(gs, "枯木岔口")
    assert loc is not None and loc["name"] == "枯木岔口"
    assert loc["kind"] == "location"
