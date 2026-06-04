"""Tests for §4.9 helper/external bonuses and §4.10 opposed checks."""
from app.content.characters import premade_pcs
from app.content.monsters import spawn
from app.engine import resolution, rules_5e
from app.engine.types import Intent, IntentTier, ResultBand
from app.state import game_state


def _fresh():
    return game_state.new_game(channel_id=1)


# ───────────────────────── §4.9 assist_bonus ─────────────────────────
def test_assist_first_helper_grants_plus_2():
    bram, lyra = premade_pcs()
    # Lyra is proficient in diplomacy → counts as a qualifying helper.
    assert rules_5e.assist_bonus([lyra], "diplomacy") == 2


def test_assist_second_helper_diminishes_to_plus_1():
    """1st +2, 2nd +1, total +3 (the cap)."""
    bram, lyra = premade_pcs()
    # Synthesise a third helper proficient in diplomacy by piggy-backing on Lyra.
    third = lyra.clone(id="npc_helper", name="Helper")
    third.skill_prof = {"diplomacy": "prof"}
    assert rules_5e.assist_bonus([lyra, third], "diplomacy") == 3


def test_assist_third_helper_caps_at_3():
    bram, lyra = premade_pcs()
    h3 = lyra.clone(id="h3", name="H3")
    h3.skill_prof = {"diplomacy": "prof"}
    h4 = lyra.clone(id="h4", name="H4")
    h4.skill_prof = {"diplomacy": "prof"}
    assert rules_5e.assist_bonus([lyra, h3, h4], "diplomacy") == 3


def test_non_proficient_helpers_grant_nothing():
    """§4.9: 協力者技能認證 — 外行幫不上忙 (untrained → +0)."""
    bram, lyra = premade_pcs()
    # Bram is NOT proficient in diplomacy; "helping" should add +0.
    assert rules_5e.assist_bonus([bram], "diplomacy") == 0


def test_assist_for_unknown_skill_returns_zero():
    """Raw ability checks have no proficiency concept → assist refuses to grant."""
    _, lyra = premade_pcs()
    assert rules_5e.assist_bonus([lyra], "improvise") == 0


# ───────────────────────── external bonus cap ─────────────────────────
def test_cap_external_clamps_above_max():
    assert rules_5e.cap_external(99) == rules_5e.EXTERNAL_BONUS_CAP
    assert rules_5e.cap_external(-99) == -rules_5e.EXTERNAL_BONUS_CAP
    assert rules_5e.cap_external(7) == 7


# ───────────────────────── ability_check external_bonus ─────────────────────────
def test_ability_check_uses_external_bonus_and_surfaces_in_breakdown():
    bram, _ = premade_pcs()
    res = rules_5e.ability_check(bram, "athletics", 30, external_bonus=4)
    # The +4 must be folded into the total; breakdown should say so.
    assert "外部 +4" in res.roll_breakdown


# ───────────────────────── §4.10 opposed_check ─────────────────────────
def test_opposed_check_dc_is_10_plus_defender_mod():
    """§4.10: 被動方轉靜態 DC = 10 + 被動 mod."""
    bram, lyra = premade_pcs()
    # Lyra's diplomacy: CHA +1, proficient +2 → +3. Static DC must be 13.
    res = rules_5e.opposed_check(bram, lyra, "intimidation", "diplomacy")
    assert res.dc == 13


def test_opposed_check_sets_target_and_summary_mentions_defense():
    bram, _ = premade_pcs()
    goblin = spawn("goblin", 1)
    res = rules_5e.opposed_check(bram, goblin, "athletics", "athletics")
    assert res.target_id == goblin.id
    assert res.target_name == goblin.name
    assert "vs" in res.summary and goblin.name in res.summary


def test_opposed_check_routes_through_four_degree():
    """Opposed checks still produce four-degree bands like ability_check."""
    bram, _ = premade_pcs()
    goblin = spawn("goblin", 1)
    bands_seen = set()
    for _ in range(40):
        res = rules_5e.opposed_check(bram, goblin, "athletics", "athletics")
        bands_seen.add(res.band)
    # Seeded RNG over 40 rolls should hit at least two of the four degrees.
    assert len(bands_seen) >= 2


# ───────────────────────── resolve() integration ─────────────────────────
def test_resolve_applies_helper_bonus_to_check():
    """A proficient helper's +2 must reach the d20 total via external_bonus."""
    gs = _fresh()
    intent = Intent(
        actor_id="pc_bram",
        raw_text="I try to persuade",  # Bram is NOT proficient in diplomacy
        tier=IntentTier.A,
        action="persuade",
        approach="diplomacy",
        target="Old Perrin",
    )
    res = resolution.resolve(gs, intent, helpers=["pc_lyra"])
    # The "[含外部 +2]" tag must appear in the breakdown.
    assert "外部 +2" in res.roll_breakdown
    # And a delta line names the helper for transparency.
    assert any("協助 +2" in d and "Lyra" in d for d in res.deltas)


def test_resolve_non_proficient_helper_does_not_boost():
    gs = _fresh()
    intent = Intent(
        actor_id="pc_lyra",
        raw_text="I sneak past",
        tier=IntentTier.A,
        action="sneak",
        approach="stealth",
        target=None,
    )
    # Neither PC is proficient in stealth → helper grants +0.
    res = resolution.resolve(gs, intent, helpers=["pc_bram"])
    assert "外部" not in res.roll_breakdown
    assert any("協助 +0" in d for d in res.deltas)


def test_resolve_external_bonus_is_capped():
    gs = _fresh()
    intent = Intent(
        actor_id="pc_lyra",
        raw_text="I persuade with everything",
        tier=IntentTier.A,
        action="persuade",
        approach="diplomacy",
    )
    # Pile on more than the cap: +2 helper + env +4 + tool +5 + resource +5 = +16.
    # Must clamp to EXTERNAL_BONUS_CAP (+10).
    res = resolution.resolve(
        gs, intent,
        helpers=["pc_bram"],   # not proficient in diplomacy → 0 from this source
        env_tier=4, tool_bonus=5, resource_spend=True,  # 4+5+5 = +14
    )
    assert f"外部 +{rules_5e.EXTERNAL_BONUS_CAP}" in res.roll_breakdown
    assert any("外部總封頂" in d for d in res.deltas)


def test_resolve_resource_spend_adds_one_tier():
    gs = _fresh()
    intent = Intent(
        actor_id="pc_lyra",
        raw_text="I push through",
        tier=IntentTier.A,
        action="persuade",
        approach="diplomacy",
    )
    res = resolution.resolve(gs, intent, resource_spend=True)
    assert f"外部 +{rules_5e.RESOURCE_SPEND_BONUS}" in res.roll_breakdown
    assert any("消耗資源" in d for d in res.deltas)
