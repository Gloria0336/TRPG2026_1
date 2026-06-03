"""Tests for the §4.4 three-band judgment and §4.7 structured costs.

These cover the small-world slice we're bringing in from AI_Living_World_design v1.0:
- margin → band classification (with nat 20 / nat 1 shift)
- back-compat `success` semantics: PARTIAL counts as success-with-cost
- scene cost_pool selection and skill-default fallback
- determinism under a seeded RNG
- JSON snapshot roundtrip preserves band + cost
"""
from app.content.characters import premade_pcs
from app.engine import dice, resolution, rules_5e
from app.engine.types import (
    Cost,
    CostSeverity,
    CostType,
    Intent,
    IntentTier,
    ResolutionResult,
    ResultBand,
    ResultKind,
)
from app.state import game_state


# ───────────────────────── pure band classification ─────────────────────────
def test_classify_band_at_or_above_dc_is_success():
    assert rules_5e.classify_band(15, 15) is ResultBand.SUCCESS
    assert rules_5e.classify_band(20, 15) is ResultBand.SUCCESS


def test_classify_band_short_by_one_to_four_is_partial():
    for total in (11, 12, 13, 14):
        assert rules_5e.classify_band(total, 15) is ResultBand.PARTIAL


def test_classify_band_short_by_five_or_more_is_failure():
    assert rules_5e.classify_band(10, 15) is ResultBand.FAILURE
    assert rules_5e.classify_band(5, 15) is ResultBand.FAILURE


def test_nat_20_shifts_band_up_one_notch():
    # FAILURE → PARTIAL
    assert rules_5e.classify_band(5, 15, nat=20) is ResultBand.PARTIAL
    # PARTIAL → SUCCESS
    assert rules_5e.classify_band(13, 15, nat=20) is ResultBand.SUCCESS
    # SUCCESS stays (already top)
    assert rules_5e.classify_band(20, 15, nat=20) is ResultBand.SUCCESS


def test_nat_1_shifts_band_down_one_notch():
    # SUCCESS → PARTIAL
    assert rules_5e.classify_band(20, 15, nat=1) is ResultBand.PARTIAL
    # PARTIAL → FAILURE
    assert rules_5e.classify_band(13, 15, nat=1) is ResultBand.FAILURE
    # FAILURE stays (already bottom)
    assert rules_5e.classify_band(5, 15, nat=1) is ResultBand.FAILURE


# ───────────────────────── ability_check integration ─────────────────────────
def test_ability_check_sets_band_and_success_semantics():
    """PARTIAL must read as success=True; FAILURE as success=False (§4.4)."""
    bram, _ = premade_pcs()
    # Run a batch so we hit all three bands under the seeded RNG.
    bands_seen = set()
    for _ in range(50):
        res = rules_5e.ability_check(bram, "athletics", 15)
        bands_seen.add(res.band)
        if res.band is ResultBand.SUCCESS:
            assert res.success is True
        elif res.band is ResultBand.PARTIAL:
            # Critical semantic: partial still counts as success for downstream callers.
            assert res.success is True
        elif res.band is ResultBand.FAILURE:
            assert res.success is False
    assert ResultBand.PARTIAL in bands_seen  # seeded RNG should produce at least one


def test_ability_check_summary_contains_band_label():
    bram, _ = premade_pcs()
    # Force-pick rolls until we get one of each band, then verify the verdict label.
    for _ in range(200):
        res = rules_5e.ability_check(bram, "athletics", 15)
        assert res.band is not None
        assert res.band.value.upper() in res.summary or "SUCCESS" in res.summary or "PARTIAL" in res.summary or "FAILURE" in res.summary


# ───────────────────────── cost selection ─────────────────────────
def _fresh():
    return game_state.new_game(channel_id=1)


def test_pick_cost_returns_none_for_success():
    gs = _fresh()
    assert resolution.pick_cost(gs, "stealth", ResultBand.SUCCESS) is None


def test_pick_cost_uses_scene_cost_pool():
    gs = _fresh()  # tavern: cost_pool = ["relation", "attention", "time"]
    seen_types = set()
    for _ in range(40):
        c = resolution.pick_cost(gs, "persuasion", ResultBand.PARTIAL)
        assert c is not None
        seen_types.add(c.type)
    # All picks must come from the tavern pool — never something off-pool like TRACE.
    assert seen_types <= {CostType.RELATION, CostType.ATTENTION, CostType.TIME}


def test_pick_cost_falls_back_to_skill_default_when_pool_empty():
    gs = _fresh()
    gs.scene.cost_pool = []  # clear the scene pool
    c = resolution.pick_cost(gs, "stealth", ResultBand.PARTIAL)
    assert c is not None
    # stealth default is EXPOSURE per the §4.7 fallback table.
    assert c.type is CostType.EXPOSURE


def test_pick_cost_severity_scales_with_band():
    gs = _fresh()
    gs.scene.cost_pool = ["time"]  # pin type so we only test severity
    partial = resolution.pick_cost(gs, "investigation", ResultBand.PARTIAL)
    failure = resolution.pick_cost(gs, "investigation", ResultBand.FAILURE)
    fumble_fail = resolution.pick_cost(gs, "investigation", ResultBand.FAILURE, fumble=True)
    assert partial.severity is CostSeverity.LIGHT
    assert failure.severity is CostSeverity.MODERATE
    assert fumble_fail.severity is CostSeverity.HEAVY


def test_pick_cost_is_deterministic_under_seed():
    gs = _fresh()
    dice.reseed(999)
    first = [resolution.pick_cost(gs, "persuasion", ResultBand.PARTIAL).type for _ in range(10)]
    dice.reseed(999)
    second = [resolution.pick_cost(gs, "persuasion", ResultBand.PARTIAL).type for _ in range(10)]
    assert first == second


# ───────────────────────── resolve() end-to-end ─────────────────────────
def test_resolve_attaches_cost_on_partial_or_failure():
    gs = _fresh()
    # Run many resolves; whenever band ≠ SUCCESS the result must carry a Cost and a
    # "代價：…" entry in deltas (the dashboard reads deltas).
    saw_non_success_with_cost = False
    for _ in range(60):
        intent = Intent(
            actor_id="pc_lyra",
            raw_text="I persuade the merchant",
            tier=IntentTier.A,
            action="persuade",
            approach="persuasion",
            target="Old Perrin",
        )
        res = resolution.resolve(gs, intent)
        if res.band is ResultBand.SUCCESS:
            assert res.cost is None
        else:
            assert res.cost is not None, f"missing cost on {res.band}"
            assert any(d.startswith("代價：") for d in res.deltas)
            saw_non_success_with_cost = True
    assert saw_non_success_with_cost


def test_resolution_result_roundtrips_band_and_cost():
    """JSON snapshot/restore must preserve band + cost so dashboards & history work."""
    res = ResolutionResult(
        kind=ResultKind.CHECK,
        actor_id="pc_lyra",
        actor_name="Lyra",
        summary="Persuasion check vs DC 13: PARTIAL",
        success=True,
        band=ResultBand.PARTIAL,
        cost=Cost(type=CostType.RELATION, severity=CostSeverity.LIGHT, note="輕微的關係惡化"),
    )
    d = res.to_dict()
    assert d["band"] == "partial"
    assert d["cost"] == {
        "type": "relation",
        "severity": "light",
        "persistent": False,
        "note": "輕微的關係惡化",
    }


def test_game_state_snapshot_preserves_cost_pool():
    gs = _fresh()
    snap = gs.to_dict()
    gs2 = game_state.GameState.from_dict(snap)
    assert gs2.scene.cost_pool == gs.scene.cost_pool


# ───────────────────────── DC ladder ─────────────────────────
def test_dc_ladder_is_six_tier():
    """§4.3 六級階梯：5/10/15/20/25/30（移除舊有 35）。"""
    assert rules_5e.DC_ANCHORS == [5, 10, 15, 20, 25, 30]
    assert 35 not in rules_5e.DC_ANCHORS
    assert rules_5e.nearest_anchor(40) == 30
