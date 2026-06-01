from app.engine import resolution
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


def test_determine_dc_uses_scene_table():
    gs = _fresh()  # tavern: persuasion DC 13
    intent = Intent(actor_id="pc_lyra", raw_text="persuade", tier=IntentTier.A, approach="persuasion")
    assert resolution.determine_dc(gs, intent, None) == 13


def test_determine_dc_snaps_proposed_to_anchor():
    gs = _fresh()
    intent = Intent(actor_id="pc_bram", raw_text="balance on a beam", tier=IntentTier.A, approach="acrobatics")
    # acrobatics not in tavern table → proposed DC 14 snaps to 15
    assert resolution.determine_dc(gs, intent, 14) == 15


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
