from app.engine import resolution, rules_5e
from app.engine.types import Intent, IntentTier, ResultKind
from app.state import game_state


def _fresh():
    return game_state.new_game(channel_id=1)


def test_new_game_setup():
    gs = _fresh()
    assert len(gs.pcs()) == 2
    assert gs.scene.id == "tavern"
    assert gs.started


def test_resolve_logs_event():
    gs = _fresh()
    before = len(gs.event_log)
    intent = Intent(actor_id="pc_lyra", raw_text="I persuade Perrin", tier=IntentTier.A,
                    action="persuade", approach="persuasion", target="Old Perrin")
    res = resolution.resolve(gs, intent)
    assert res.kind is ResultKind.CHECK
    assert len(gs.event_log) == before + 1
    assert gs.event_log[-1].data["kind"] == "check"


def test_narrative_beat_logs_no_roll_result():
    gs = _fresh()
    before = len(gs.event_log)
    pc = gs.characters["pc_lyra"]
    res = resolution.narrative_beat(gs, pc, "walk to the window",
                                    target_name="窗邊", raw_text="我走到窗邊看看外面")
    assert res.kind is ResultKind.NARRATIVE
    assert res.success is None and res.band is None
    assert res.roll_breakdown is None and res.dc is None
    assert res.target_name == "窗邊"
    # logged before target/raw_text would be lost, so the event carries them
    assert len(gs.event_log) == before + 1
    assert gs.event_log[-1].data["kind"] == "narrative"


def test_requires_check_trivial_action_is_free():
    gs = _fresh()  # tavern
    # No target, no contested skill, not a scene challenge → genuinely no roll.
    intent = Intent(actor_id="pc_lyra", raw_text="I walk over to the window",
                    tier=IntentTier.A, action="walk to the window")
    assert resolution.requires_check(gs, intent) is False


def test_requires_check_forces_roll_for_contested_skill():
    gs = _fresh()
    intent = Intent(actor_id="pc_lyra", raw_text="I sneak past", tier=IntentTier.A,
                    action="sneak", approach="stealth", needs_check=False)
    assert resolution.requires_check(gs, intent) is True


def test_requires_check_forces_roll_for_attack():
    gs = _fresh()
    intent = Intent(actor_id="pc_lyra", raw_text="I attack", tier=IntentTier.A,
                    is_attack=True, needs_check=False)
    assert resolution.requires_check(gs, intent) is True


def test_requires_check_free_for_reading_held_map():
    # tavern lists perception as a DC-15 challenge, but a map you hold is not a scene
    # entity — a bare scene-wide skill DC must NOT force a roll for reading it.
    gs = _fresh()
    intent = Intent(actor_id="pc_lyra", raw_text="我查看手上的地圖",
                    tier=IntentTier.A, action="look", approach="perception",
                    target="地圖", needs_check=False)
    assert resolution.requires_check(gs, intent) is False


def test_requires_check_free_for_look_without_target():
    # Glancing around with no specific scene obstacle is a free beat now.
    gs = _fresh()
    intent = Intent(actor_id="pc_lyra", raw_text="I look around the room",
                    tier=IntentTier.A, action="look", approach="perception", needs_check=False)
    assert resolution.requires_check(gs, intent) is False


def test_requires_check_forces_roll_examining_present_scene_entity():
    # Engaging a PRESENT scene entity (老佩林) with a skill the scene flags (perception)
    # still rolls — the surgical replacement for the old blanket scene-challenge force.
    gs = _fresh()
    intent = Intent(actor_id="pc_lyra", raw_text="我仔細打量老佩林",
                    tier=IntentTier.A, action="look", approach="perception",
                    target="老佩林", needs_check=False)
    assert resolution.requires_check(gs, intent) is True


def test_requires_check_forces_roll_against_wary_target():
    gs = _fresh()  # tavern: the hooded figure is 'afraid' (an opposed disposition)
    intent = Intent(actor_id="pc_lyra", raw_text="I hand the hooded figure a drink",
                    tier=IntentTier.A, action="offer drink", target="兜帽客", needs_check=False)
    assert resolution.requires_check(gs, intent) is True


def test_determine_dc_uses_scene_table():
    gs = _fresh()
    intent = Intent(actor_id="pc_lyra", raw_text="persuade", tier=IntentTier.A, approach="persuasion")
    assert resolution.determine_dc(gs, intent, None) == gs.scene.challenges["persuasion"]


def test_determine_dc_from_assessment():
    from app.ai.schemas import DCAssessment
    gs = _fresh()
    intent = Intent(actor_id="pc_bram", raw_text="kick the door", tier=IntentTier.A, approach="athletics")
    # athletics not in tavern table → use the AI assessment's final DC verbatim (no snap).
    a = DCAssessment(base_dc=25, env_modifier=4, final_dc=29, env_reason="監牢大門")
    assert resolution.determine_dc(gs, intent, a) == 29


def test_determine_dc_can_drop_below_ladder_floor():
    from app.ai.schemas import DCAssessment
    gs = _fresh()
    intent = Intent(actor_id="pc_bram", raw_text="pick the simple lock", tier=IntentTier.A,
                    approach="sleight_of_hand")
    # right tool on an easy target: base 5 − 3 → DC 2 (intentionally not snapped up to 5).
    a = DCAssessment(base_dc=5, env_modifier=-3, final_dc=2, env_reason="普通木門")
    assert resolution.determine_dc(gs, intent, a) == 2


def test_determine_dc_defaults_to_normal():
    gs = _fresh()
    # acrobatics not in tavern table, no assessment → default normal=15.
    intent = Intent(actor_id="pc_bram", raw_text="balance on a beam", tier=IntentTier.A, approach="acrobatics")
    assert resolution.determine_dc(gs, intent, None) == 15


def test_determine_dc_applies_npc_disposition_offset():
    gs = _fresh()
    # 老佩林 is friendly (−3); persuasion scene DC is 11 → 11 − 3 = 8.
    friendly = Intent(actor_id="pc_lyra", raw_text="說服老佩林", tier=IntentTier.A,
                      approach="persuasion", target="老佩林")
    assert resolution.determine_dc(gs, friendly, None) == 8
    # 兜帽客 is afraid (−1); persuasion scene DC 11 → 10.
    afraid = Intent(actor_id="pc_lyra", raw_text="說服兜帽客", tier=IntentTier.A,
                    approach="persuasion", target="兜帽客")
    assert resolution.determine_dc(gs, afraid, None) == 10


def test_determine_dc_npc_offset_only_for_social_skills():
    gs = _fresh()
    # stealth is not a social skill → no disposition offset even against a friendly NPC.
    intent = Intent(actor_id="pc_lyra", raw_text="溜過老佩林", tier=IntentTier.A,
                    approach="stealth", target="老佩林")
    assert resolution.determine_dc(gs, intent, None) == 15  # default normal, unmodified


def test_determine_dc_npc_offset_stacks_with_assessment_and_floors():
    from app.ai.schemas import DCAssessment
    gs = _fresh()
    # AI assessment final 2 (base 5, env −3) + friendly −3 = −1 → floored at MIN_DC.
    intent = Intent(actor_id="pc_lyra", raw_text="說服老佩林", tier=IntentTier.A,
                    approach="persuasion", target="老佩林")
    a = DCAssessment(base_dc=5, env_modifier=-3, final_dc=2, env_reason="輕鬆")
    assert resolution.determine_dc(gs, intent, a) == rules_5e.MIN_DC


def test_resolve_records_npc_disposition_audit():
    gs = _fresh()
    intent = Intent(actor_id="pc_lyra", raw_text="說服老佩林", tier=IntentTier.A,
                    action="persuade", approach="persuasion", target="老佩林")
    res = resolution.resolve(gs, intent)
    assert res.dc_npc_modifier == -3
    assert res.dc_npc_disposition == "friendly"
    # friendly −3 off the persuasion scene DC of 11.
    assert res.dc == 8


def test_normalize_approach_synonyms():
    assert resolution.normalize_approach("lockpick") == "sleight_of_hand"
    assert resolution.normalize_approach("convince") == "persuasion"
    assert resolution.normalize_approach("stealth") == "stealth"
    assert resolution.normalize_approach("I try to sneak past") == "stealth"


def test_snapshot_roundtrip():
    gs = _fresh()
    intent = Intent(actor_id="pc_bram", raw_text="search", tier=IntentTier.A, approach="investigation")
    resolution.resolve(gs, intent)
    d = gs.to_dict()
    gs2 = game_state.GameState.from_dict(d)
    assert gs2.scene.id == gs.scene.id
    assert len(gs2.event_log) == len(gs.event_log)
    assert gs2.characters["pc_bram"].name == "Bram Ironwood"
    assert gs2.characters["pc_bram"].find_action("Longsword") is not None


def test_snapshot_load_clears_unresumable_pending_action():
    gs = _fresh()
    gs.claim_pc("user-1", "pc_bram")
    gs.begin_freeplay_action("pc_bram")

    gs2 = game_state.GameState.from_dict(gs.to_dict())

    assert gs2.pending_freeplay_actor_id() is None


def test_active_campaign_tracks_started_not_over_state():
    game_state.set_state(None)
    assert not game_state.has_active_campaign()

    gs = game_state.reset_state(channel_id=1)
    assert game_state.has_active_campaign()

    gs.flags["over"] = True
    assert not game_state.has_active_campaign()
    game_state.set_state(None)


def test_active_campaign_channel_helpers_ignore_unbound_channels():
    game_state.set_state(None)

    gs = game_state.reset_state(channel_id=0)
    assert game_state.has_active_campaign()
    assert game_state.active_campaign() is gs
    assert not game_state.has_discord_channel_binding(gs)
    assert game_state.active_campaign_for_channel(123) is None

    gs.channel_id = 123
    assert game_state.has_discord_channel_binding(gs)
    assert game_state.active_campaign_for_channel(123) is gs
    assert game_state.active_campaign_for_channel(456) is None

    game_state.set_state(None)


def test_claim_pc_prevents_two_players_using_same_character():
    gs = _fresh()
    assert gs.claim_pc("user-1", "pc_bram")
    assert not gs.claim_pc("user-2", "pc_bram")
    assert gs.pc_for_user("user-1").id == "pc_bram"
    assert gs.pc_for_user("user-2") is None


def test_freeplay_turn_order_waits_for_next_player():
    gs = _fresh()
    assert gs.claim_pc("user-1", "pc_lyra")
    assert gs.claim_pc("user-2", "pc_bram")

    assert gs.freeplay_turn_order() == ["pc_lyra", "pc_bram"]
    assert gs.current_freeplay_actor_id() == "pc_lyra"

    gs.begin_freeplay_action("pc_lyra")
    assert gs.pending_freeplay_actor_id() == "pc_lyra"

    assert gs.complete_freeplay_action("pc_lyra") == "pc_bram"
    assert gs.pending_freeplay_actor_id() is None

    assert gs.complete_freeplay_action("pc_bram") == "pc_lyra"
    assert gs.flags["freeplay_round"] == 2
