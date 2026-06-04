"""Tests for the §4.4 four-degree judgment and §4.4/§4.7 structured cost & boon.

These cover the PF2e-aligned slice of AI_Living_World_design v1.0:
- margin → four-degree classification (symmetric ±10, with nat 20 / nat 1 shift)
- back-compat `success` semantics: a success degree (crit-success/success) → True
- scene cost_pool selection and skill-default fallback on failure degrees
- scene boon_pool / skill-default boon on a crit-success
- determinism under a seeded RNG
- JSON snapshot roundtrip preserves band + cost + boon
"""
from app.content.characters import premade_pcs
from app.engine import dice, resolution, rules_5e
from app.engine.types import (
    Boon,
    BoonMagnitude,
    BoonType,
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
def test_classify_band_at_or_above_dc_plus_10_is_crit_success():
    assert rules_5e.classify_band(25, 15) is ResultBand.CRIT_SUCCESS
    assert rules_5e.classify_band(30, 15) is ResultBand.CRIT_SUCCESS


def test_classify_band_at_or_above_dc_is_success():
    assert rules_5e.classify_band(15, 15) is ResultBand.SUCCESS
    assert rules_5e.classify_band(24, 15) is ResultBand.SUCCESS


def test_classify_band_below_dc_is_failure():
    for total in (6, 10, 14):
        assert rules_5e.classify_band(total, 15) is ResultBand.FAILURE


def test_classify_band_at_or_below_dc_minus_10_is_crit_failure():
    assert rules_5e.classify_band(5, 15) is ResultBand.CRIT_FAILURE
    assert rules_5e.classify_band(0, 15) is ResultBand.CRIT_FAILURE


def test_nat_20_shifts_band_up_one_notch():
    # CRIT_FAILURE → FAILURE
    assert rules_5e.classify_band(5, 15, nat=20) is ResultBand.FAILURE
    # FAILURE → SUCCESS
    assert rules_5e.classify_band(14, 15, nat=20) is ResultBand.SUCCESS
    # SUCCESS → CRIT_SUCCESS
    assert rules_5e.classify_band(24, 15, nat=20) is ResultBand.CRIT_SUCCESS
    # CRIT_SUCCESS stays (already top)
    assert rules_5e.classify_band(25, 15, nat=20) is ResultBand.CRIT_SUCCESS


def test_nat_1_shifts_band_down_one_notch():
    # CRIT_SUCCESS → SUCCESS
    assert rules_5e.classify_band(25, 15, nat=1) is ResultBand.SUCCESS
    # SUCCESS → FAILURE
    assert rules_5e.classify_band(15, 15, nat=1) is ResultBand.FAILURE
    # FAILURE → CRIT_FAILURE
    assert rules_5e.classify_band(6, 15, nat=1) is ResultBand.CRIT_FAILURE
    # CRIT_FAILURE stays (already bottom)
    assert rules_5e.classify_band(5, 15, nat=1) is ResultBand.CRIT_FAILURE


# ───────────────────────── ability_check integration ─────────────────────────
def test_ability_check_sets_band_and_success_semantics():
    """A success degree must read as success=True; a failure degree as False (§4.4)."""
    bram, _ = premade_pcs()
    bands_seen = set()
    for _ in range(200):
        res = rules_5e.ability_check(bram, "athletics", 15)
        bands_seen.add(res.band)
        if res.band in (ResultBand.CRIT_SUCCESS, ResultBand.SUCCESS):
            assert res.success is True
        else:
            assert res.success is False
    # The seeded RNG should produce at least an ordinary success and an ordinary failure.
    assert ResultBand.SUCCESS in bands_seen
    assert ResultBand.FAILURE in bands_seen


def test_ability_check_summary_contains_band_label():
    bram, _ = premade_pcs()
    labels = {"CRIT SUCCESS", "SUCCESS", "FAILURE", "CRIT FAILURE"}
    for _ in range(200):
        res = rules_5e.ability_check(bram, "athletics", 15)
        assert res.band is not None
        assert any(lbl in res.summary for lbl in labels)


# ───────────────────────── cost selection ─────────────────────────
def _fresh():
    return game_state.new_game(channel_id=1)


def test_pick_cost_returns_none_for_success_degrees():
    gs = _fresh()
    assert resolution.pick_cost(gs, "stealth", ResultBand.SUCCESS) is None
    assert resolution.pick_cost(gs, "stealth", ResultBand.CRIT_SUCCESS) is None


def test_pick_cost_uses_scene_cost_pool():
    gs = _fresh()  # tavern: cost_pool = ["relation", "attention", "time"]
    seen_types = set()
    for _ in range(40):
        c = resolution.pick_cost(gs, "diplomacy", ResultBand.FAILURE)
        assert c is not None
        seen_types.add(c.type)
    # All picks must come from the tavern pool — never something off-pool like TRACE.
    assert seen_types <= {CostType.RELATION, CostType.ATTENTION, CostType.TIME}


def test_pick_cost_falls_back_to_skill_default_when_pool_empty():
    gs = _fresh()
    gs.scene.cost_pool = []  # clear the scene pool
    c = resolution.pick_cost(gs, "stealth", ResultBand.FAILURE)
    assert c is not None
    # stealth default is EXPOSURE per the §4.7 fallback table.
    assert c.type is CostType.EXPOSURE


def test_pick_cost_severity_scales_with_band():
    gs = _fresh()
    gs.scene.cost_pool = ["time"]  # pin type so we only test severity
    failure = resolution.pick_cost(gs, "perception", ResultBand.FAILURE)
    crit_fail = resolution.pick_cost(gs, "perception", ResultBand.CRIT_FAILURE)
    assert failure.severity is CostSeverity.MODERATE
    assert failure.persistent is False
    assert crit_fail.severity is CostSeverity.HEAVY
    # A crit failure cost flows into durable state.
    assert crit_fail.persistent is True


def test_pick_cost_is_deterministic_under_seed():
    gs = _fresh()
    dice.reseed(999)
    first = [resolution.pick_cost(gs, "diplomacy", ResultBand.FAILURE).type for _ in range(10)]
    dice.reseed(999)
    second = [resolution.pick_cost(gs, "diplomacy", ResultBand.FAILURE).type for _ in range(10)]
    assert first == second


# ───────────────────────── boon selection ─────────────────────────
def test_pick_boon_only_on_crit_success():
    gs = _fresh()
    assert resolution.pick_boon(gs, "stealth", ResultBand.SUCCESS) is None
    assert resolution.pick_boon(gs, "stealth", ResultBand.FAILURE) is None
    boon = resolution.pick_boon(gs, "stealth", ResultBand.CRIT_SUCCESS)
    assert boon is not None
    # A crit-success boon is MAJOR and persists into durable state (§4.4 大成功額外效果).
    assert boon.magnitude is BoonMagnitude.MAJOR
    assert boon.persistent is True


def test_pick_boon_falls_back_to_skill_default():
    gs = _fresh()
    gs.scene.boon_pool = []
    # diplomacy default boon is GOODWILL per the fallback table.
    boon = resolution.pick_boon(gs, "diplomacy", ResultBand.CRIT_SUCCESS)
    assert boon.type is BoonType.GOODWILL


# ───────────────────────── resolve() end-to-end ─────────────────────────
def test_resolve_attaches_cost_on_failure_and_boon_on_crit_success():
    gs = _fresh()
    saw_failure_with_cost = False
    saw_success_clean = False
    for _ in range(80):
        intent = Intent(
            actor_id="pc_lyra",
            raw_text="I persuade the merchant",
            tier=IntentTier.A,
            action="persuade",
            approach="diplomacy",
            target="Old Perrin",
        )
        res = resolution.resolve(gs, intent)
        if res.band in (ResultBand.FAILURE, ResultBand.CRIT_FAILURE):
            assert res.cost is not None, f"missing cost on {res.band}"
            assert any(d.startswith("代價：") for d in res.deltas)
            saw_failure_with_cost = True
        elif res.band is ResultBand.CRIT_SUCCESS:
            assert res.boon is not None
            assert any(d.startswith("增益：") for d in res.deltas)
        elif res.band is ResultBand.SUCCESS:
            assert res.cost is None
            saw_success_clean = True
    assert saw_failure_with_cost
    assert saw_success_clean


def test_resolution_result_roundtrips_band_cost_and_boon():
    """JSON snapshot/restore must preserve band + cost + boon so dashboards & history work."""
    res = ResolutionResult(
        kind=ResultKind.CHECK,
        actor_id="pc_lyra",
        actor_name="Lyra",
        summary="Diplomacy check vs DC 13: FAILURE",
        success=False,
        band=ResultBand.FAILURE,
        cost=Cost(type=CostType.RELATION, severity=CostSeverity.MODERATE, note="中等的關係惡化"),
    )
    d = res.to_dict()
    assert d["band"] == "failure"
    assert d["cost"] == {
        "type": "relation",
        "severity": "moderate",
        "persistent": False,
        "note": "中等的關係惡化",
    }

    crit = ResolutionResult(
        kind=ResultKind.CHECK,
        actor_id="pc_lyra",
        actor_name="Lyra",
        summary="Stealth check vs DC 13: CRIT SUCCESS",
        success=True,
        band=ResultBand.CRIT_SUCCESS,
        boon=Boon(type=BoonType.OPENING, magnitude=BoonMagnitude.MAJOR, persistent=True, note="顯著的創造良機"),
    )
    cd = crit.to_dict()
    assert cd["band"] == "crit_success"
    assert cd["boon"] == {
        "type": "opening",
        "magnitude": "major",
        "persistent": True,
        "note": "顯著的創造良機",
    }


def test_legacy_partial_band_loads_as_success():
    """A snapshot from the old three-band model must not crash on load (§4.4 shim)."""
    assert ResultBand("partial") is ResultBand.SUCCESS


def test_game_state_snapshot_preserves_cost_pool():
    gs = _fresh()
    snap = gs.to_dict()
    gs2 = game_state.GameState.from_dict(snap)
    assert gs2.scene.cost_pool == gs.scene.cost_pool


# ───────────────────────── DC ladder ─────────────────────────
def test_dc_ladder_is_four_tier():
    """§4.3 四級階梯：容易5/標準10/困難15/極難20（標準＝中間數 10）。"""
    assert rules_5e.DC_ANCHORS == [5, 10, 15, 20]
    assert rules_5e.BAND_DC["normal"] == 10
    assert 25 not in rules_5e.DC_ANCHORS and 30 not in rules_5e.DC_ANCHORS
    assert rules_5e.nearest_anchor(40) == 20
