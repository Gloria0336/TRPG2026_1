from app.content.characters import premade_pcs
from app.db import store
from app.engine import guild_rank, resolution
from app.engine.types import Intent, IntentTier
from app.state import game_state


def test_merit_triggers_promotion_then_promote_spends_threshold():
    bram, _ = premade_pcs()

    guild_rank.award_merit(bram, guild_rank.RANK_THRESHOLDS["E"])

    assert bram.rank_flags["promotion_available"] == "E"
    assert guild_rank.promote(bram) == "E"
    assert bram.guild_rank == "E"
    assert bram.merit == 0


def test_rank_gate_and_under_rank_penalty():
    bram, _ = premade_pcs()

    assert guild_rank.rank_gate("F", bram)
    assert not guild_rank.rank_gate("C", bram)
    assert guild_rank.under_rank_dc_penalty("C", bram) == 6


def test_institutional_consequence_does_not_reduce_rank_or_skills():
    bram, _ = premade_pcs()
    bram.guild_rank = "C"
    before_skill = bram.skill_prof.copy()

    cost = guild_rank.institutional_consequence(bram, severe=True)

    assert bram.guild_rank == "C"
    assert bram.skill_prof == before_skill
    assert bram.standing == -2
    assert bram.rank_flags["suspended"] is True
    assert cost.type.value == "debt"


def test_soft_rank_gate_adds_dc_penalty_from_entity_flags():
    gs = game_state.reset_state(channel_id=1)
    store.upsert_entity(
        id="high_gate",
        scene_id=gs.current_location_id,
        kind="person",
        name="高階委託官",
        aliases=["委託官"],
        flags={"min_rank": "C", "gate": "soft"},
    )
    intent = Intent(
        actor_id="pc_bram",
        raw_text="說服高階委託官",
        tier=IntentTier.A,
        action="persuade",
        approach="diplomacy",
        target="委託官",
    )

    assert resolution.determine_dc(gs, intent, None) == 16
    res = resolution.resolve(gs, intent)
    assert any("階級壓力" in d and "DC +6" in d for d in res.deltas)


def test_complete_quest_awards_skill_points_and_merit():
    gs = game_state.reset_state(channel_id=1)
    quest = store.upsert_quest_seed(
        dedupe_key="reward",
        scene_id=gs.current_location_id,
        giver="公會",
        seed={
            "title_hint": "測試任務",
            "tags": {"risk_level": "moderate", "reward_sp": 3, "reward_merit": 12},
        },
    )

    gs.complete_quest(quest["id"], actor_id="pc_bram")

    assert store.get_quest(quest["id"])["status"] == "completed"
    assert all(pc.skill_points == 3 for pc in gs.pcs())
    assert all(pc.merit == 12 for pc in gs.pcs())
