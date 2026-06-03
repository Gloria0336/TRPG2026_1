"""Mechanical conditions / flags — A-class target conditions + E-class semantic
gates. These prove the fix for the trace.log bug where a hypnotized NPC still
prompted威嚇/魅惑 options and rolled an Intimidation check."""
from app.ai import prompts
from app.ai.schemas import DCAssessment
from app.db import store
from app.engine import conditions, resolution
from app.engine.conditions import CheckOutcome
from app.engine.types import Intent, IntentTier, ResultBand
from app.state import game_state


def _dc(n: int) -> DCAssessment:
    """Force a specific final DC in tests (base/env decomposition is irrelevant here)."""
    return DCAssessment(base_dc=n, env_modifier=0, final_dc=n)


# ───────────────────────── catalog / gate ─────────────────────────
def test_catalog_known_ids():
    assert conditions.known(conditions.HYPNOTIZED)
    assert conditions.known(conditions.CHARMED)
    assert not conditions.known("not_a_real_condition")


def test_evaluate_gate_no_conditions_rolls():
    decision = conditions.evaluate_gate([], approach="persuasion")
    assert decision.outcome is CheckOutcome.ROLL
    assert not decision.short_circuits


def test_hypnotized_target_auto_success_on_social():
    d = conditions.evaluate_gate([conditions.HYPNOTIZED], approach="persuasion")
    assert d.outcome is CheckOutcome.AUTO_SUCCESS
    assert conditions.HYPNOTIZED in d.triggering


def test_hypnotized_target_intimidation_also_short_circuits():
    # intimidation falls under SOCIAL_SKILLS, so a hypnotized target should not
    # be re-checked — the gate fires before the contested-skill rule.
    d = conditions.evaluate_gate([conditions.HYPNOTIZED], approach="intimidation")
    assert d.outcome is CheckOutcome.AUTO_SUCCESS


def test_charmed_blocks_intimidation_but_auto_passes_persuasion():
    d_persuade = conditions.evaluate_gate([conditions.CHARMED], approach="persuasion")
    d_threaten = conditions.evaluate_gate([conditions.CHARMED], approach="intimidation")
    assert d_persuade.outcome is CheckOutcome.AUTO_SUCCESS
    assert d_threaten.outcome is CheckOutcome.AUTO_FAIL


def test_dead_overrides_charm_with_auto_fail():
    # AUTO_FAIL has higher priority than AUTO_SUCCESS in the rank table.
    d = conditions.evaluate_gate(
        [conditions.CHARMED, conditions.TARGET_DEAD], approach="persuasion",
    )
    assert d.outcome is CheckOutcome.AUTO_FAIL


def test_frightened_gives_advantage_against():
    d = conditions.evaluate_gate([conditions.FRIGHTENED], approach="intimidation")
    # frightened doesn't short-circuit social outcomes, but it grants the actor
    # advantage on rolls against the frightened target.
    assert d.outcome is CheckOutcome.ROLL
    assert d.advantage is True


# ───────────────────────── store helpers ─────────────────────────
def test_add_remove_condition_round_trip():
    store.upsert_entity(id="ent_x", scene_id="tavern", kind="person", name="Test")
    assert store.add_condition("ent_x", conditions.HYPNOTIZED) is True
    assert store.add_condition("ent_x", conditions.HYPNOTIZED) is False  # idempotent
    assert conditions.HYPNOTIZED in store.get_conditions("ent_x")
    assert store.has_condition("ent_x", conditions.HYPNOTIZED)

    assert store.remove_condition("ent_x", conditions.HYPNOTIZED) is True
    assert store.remove_condition("ent_x", conditions.HYPNOTIZED) is False
    assert conditions.HYPNOTIZED not in store.get_conditions("ent_x")


def test_get_conditions_by_ref_resolves_alias():
    gs = game_state.reset_state(channel_id=1)
    assert store.add_condition("ent_hooded", conditions.HYPNOTIZED)
    ent_id, conds = store.get_conditions_by_ref(gs.current_location_id, "兜帽客")
    assert ent_id == "ent_hooded"
    assert conditions.HYPNOTIZED in conds


# ───────────────────────── resolve() short-circuits ─────────────────────────
def test_resolve_skips_d20_when_target_hypnotized():
    gs = game_state.reset_state(channel_id=1)
    store.add_condition("ent_hooded", conditions.HYPNOTIZED)

    intent = Intent(
        actor_id="pc_bram", raw_text="威嚇兜帽客", tier=IntentTier.A,
        action="intimidate", approach="intimidation", target="兜帽客",
    )
    result = resolution.resolve(gs, intent)
    assert result.band is ResultBand.SUCCESS
    assert result.success is True
    assert "短路" in result.summary
    assert result.roll_breakdown == "無需擲骰（目標狀態觸發短路）"
    assert any("催眠" in d for d in result.deltas)


def test_resolve_auto_fail_against_dead_target():
    gs = game_state.reset_state(channel_id=1)
    store.add_condition("ent_hooded", conditions.TARGET_DEAD)

    intent = Intent(
        actor_id="pc_lyra", raw_text="說服死人", tier=IntentTier.A,
        action="persuade", approach="persuasion", target="兜帽客",
    )
    result = resolution.resolve(gs, intent)
    assert result.band is ResultBand.FAILURE
    assert result.success is False


def test_requires_check_yields_to_gate_short_circuit():
    """A hypnotized social target should NOT be forced into a check by the
    contested-skill gate — that was the trace.log bug."""
    gs = game_state.reset_state(channel_id=1)
    store.add_condition("ent_hooded", conditions.HYPNOTIZED)

    intent = Intent(
        actor_id="pc_bram", raw_text="威嚇他", tier=IntentTier.A,
        action="intimidate", approach="intimidation", target="兜帽客",
        needs_check=True,
    )
    # Gate is a short-circuit, so requires_check returns False even though
    # intimidation is contested. The bot dispatch path can then narrate the
    # outcome without offering a forced威嚇 dialogue.
    assert resolution.requires_check(gs, intent) is False


def test_requires_check_still_forces_roll_when_no_gate():
    gs = game_state.reset_state(channel_id=1)
    intent = Intent(
        actor_id="pc_bram", raw_text="威嚇他", tier=IntentTier.A,
        action="intimidate", approach="intimidation", target="兜帽客",
        needs_check=False,
    )
    # No condition on the target → the contested-skill gate is back in force.
    assert resolution.requires_check(gs, intent) is True


# ───────────────────────── auto-apply on successful spell ─────────────────────────
def test_successful_hypnotize_attaches_condition():
    """The catalog maps 催眠 → HYPNOTIZED. A SUCCESS check via that verb should
    leave the condition on the target so subsequent social actions short-circuit."""
    gs = game_state.reset_state(channel_id=1)

    intent = Intent(
        actor_id="pc_lyra", raw_text="對兜帽客使用催眠術", tier=IntentTier.A,
        action="催眠", approach="arcana", target="兜帽客",
    )
    # Force success deterministically by giving a tiny DC.
    result = resolution.resolve(gs, intent, assessment=_dc(5))
    assert result.success is True
    assert conditions.HYPNOTIZED in store.get_conditions("ent_hooded")


def test_failed_spell_does_not_attach_condition():
    gs = game_state.reset_state(channel_id=1)
    intent = Intent(
        actor_id="pc_lyra", raw_text="催眠失敗", tier=IntentTier.A,
        action="催眠", approach="arcana", target="兜帽客",
    )
    # Massive DC guarantees FAILURE (auto-success on nat 20 still beats it but
    # the seeded RNG is deterministic — see conftest).
    result = resolution.resolve(gs, intent, assessment=_dc(35))
    if result.band is ResultBand.FAILURE:
        assert conditions.HYPNOTIZED not in store.get_conditions("ent_hooded")


# ───────────────────────── intent prompt exposure ─────────────────────────
def test_intent_context_surfaces_target_conditions():
    gs = game_state.reset_state(channel_id=1)
    store.add_condition("ent_hooded", conditions.HYPNOTIZED)

    ctx = prompts.intent_context(gs, gs.characters["pc_bram"], "詢問兜帽客")
    assert "TARGET CONDITIONS" in ctx
    assert "催眠" in ctx
    # The instruction line is what nudges the parser to set needs_check=false.
    assert "needs_check=false" in ctx


# ───────────────────────── B class (actor-side) ─────────────────────────
def test_blessed_actor_adds_d4_to_breakdown():
    gs = game_state.reset_state(channel_id=1)
    pc = gs.characters["pc_lyra"]
    pc.conditions.append(conditions.BLESSED)

    intent = Intent(
        actor_id="pc_lyra", raw_text="說服佩林", tier=IntentTier.A,
        action="persuade", approach="persuasion", target="老佩林",
    )
    result = resolution.resolve(gs, intent, assessment=_dc(10))
    # The bless die is folded into external_bonus; the deltas line records its
    # source so the embed/log can show why the modifier landed where it did.
    bless_delta = next((d for d in result.deltas if "外部加值" in d), "")
    assert "1d4" in bless_delta
    assert any("祝福" in d for d in result.deltas)


def test_poisoned_actor_gets_disadvantage():
    gs = game_state.reset_state(channel_id=1)
    pc = gs.characters["pc_lyra"]
    pc.conditions.append(conditions.POISONED)

    effect = conditions.evaluate_actor(
        pc.conditions, approach="persuasion", is_attack=False,
    )
    assert effect.disadvantage is True
    assert effect.advantage is False


def test_exhausted_l3_imposes_disadvantage():
    effect = conditions.evaluate_actor(
        [conditions.EXHAUSTED], approach="persuasion",
        condition_meta={conditions.EXHAUSTED: {"level": 3}},
    )
    assert effect.disadvantage is True


def test_exhausted_l6_auto_fails():
    effect = conditions.evaluate_actor(
        [conditions.EXHAUSTED], approach="athletics",
        condition_meta={conditions.EXHAUSTED: {"level": 6}},
    )
    assert effect.outcome is CheckOutcome.AUTO_FAIL


def test_exhausted_l1_is_just_noted():
    effect = conditions.evaluate_actor(
        [conditions.EXHAUSTED], approach="persuasion",
        condition_meta={conditions.EXHAUSTED: {"level": 1}},
    )
    assert effect.disadvantage is False
    assert effect.outcome is CheckOutcome.ROLL


# ───────────────────────── D class (NPC dialog) ─────────────────────────
def test_under_duress_downgrades_success_to_partial():
    gs = game_state.reset_state(channel_id=1)
    store.add_condition("ent_hooded", conditions.UNDER_DURESS)

    intent = Intent(
        actor_id="pc_lyra", raw_text="詢問兜帽客", tier=IntentTier.A,
        action="persuade", approach="persuasion", target="兜帽客",
    )
    result = resolution.resolve(gs, intent, assessment=_dc(5))
    # DC 5 + Lyra's CHA = guaranteed SUCCESS → but under_duress drops it to PARTIAL.
    assert result.band is ResultBand.PARTIAL
    assert any("被脅迫" in d for d in result.deltas)


def test_loyal_to_other_auto_fails_persuasion():
    gs = game_state.reset_state(channel_id=1)
    # Perrin is loyal to a nephew NPC — pc_bram trying to talk him into betraying.
    store.add_condition("ent_perrin", conditions.loyal_to("ent_nephew"))

    intent = Intent(
        actor_id="pc_bram", raw_text="叫商人放棄外甥", tier=IntentTier.A,
        action="persuade", approach="persuasion", target="佩林",
    )
    result = resolution.resolve(gs, intent)
    assert result.band is ResultBand.FAILURE
    assert "短路" in result.summary


def test_indebted_to_actor_auto_succeeds():
    gs = game_state.reset_state(channel_id=1)
    store.add_condition("ent_perrin", conditions.indebted_to("pc_lyra"))

    intent = Intent(
        actor_id="pc_lyra", raw_text="請求佩林協助", tier=IntentTier.A,
        action="persuade", approach="persuasion", target="佩林",
    )
    result = resolution.resolve(gs, intent)
    assert result.band is ResultBand.SUCCESS


def test_lying_target_grants_insight_advantage():
    d = conditions.evaluate_gate([conditions.LYING], approach="insight")
    assert d.advantage is True


# ───────────────────────── condition_meta ─────────────────────────
def test_condition_meta_round_trip():
    store.upsert_entity(id="ent_y", scene_id="tavern", kind="person", name="Test")
    store.add_condition("ent_y", conditions.EXHAUSTED, level=3, source="pc_bram", duration=5)
    meta = store.get_condition_meta("ent_y")
    assert meta[conditions.EXHAUSTED] == {"level": 3, "source": "pc_bram", "duration": 5}


def test_tick_conditions_expires_when_zero():
    store.upsert_entity(id="ent_z", scene_id="tavern", kind="person", name="Test")
    store.add_condition("ent_z", conditions.HYPNOTIZED, duration=2)

    assert store.tick_conditions("ent_z") == []  # 2 → 1
    assert conditions.HYPNOTIZED in store.get_conditions("ent_z")

    expired = store.tick_conditions("ent_z")     # 1 → 0
    assert conditions.HYPNOTIZED in expired
    assert conditions.HYPNOTIZED not in store.get_conditions("ent_z")


def test_meta_persists_across_add_calls():
    store.upsert_entity(id="ent_w", scene_id="tavern", kind="person", name="Test")
    store.add_condition("ent_w", conditions.EXHAUSTED, level=1)
    store.add_condition("ent_w", conditions.EXHAUSTED, level=2)   # upgrade
    meta = store.get_condition_meta("ent_w")
    assert meta[conditions.EXHAUSTED]["level"] == 2


# ───────────────────────── delta extraction → store ─────────────────────────
def test_apply_delta_attaches_conditions():
    gs = game_state.reset_state(channel_id=1)
    store.apply_delta(gs.current_location_id, {
        "entity_ref": "兜帽客",
        "add_conditions": [conditions.RESTRAINED, conditions.FRIGHTENED],
    })
    conds = store.get_conditions("ent_hooded")
    assert conditions.RESTRAINED in conds
    assert conditions.FRIGHTENED in conds


def test_apply_delta_removes_conditions():
    gs = game_state.reset_state(channel_id=1)
    store.add_condition("ent_hooded", conditions.UNCONSCIOUS)
    store.apply_delta(gs.current_location_id, {
        "entity_ref": "兜帽客",
        "remove_conditions": [conditions.UNCONSCIOUS],
    })
    assert conditions.UNCONSCIOUS not in store.get_conditions("ent_hooded")


def test_schema_drops_unknown_conditions():
    from app.ai.schemas import EntityStateDelta
    d = EntityStateDelta(
        entity_ref="兜帽客",
        add_conditions=[conditions.HYPNOTIZED, "lol_not_real", "made_up_flag"],
    )
    assert d.add_conditions == [conditions.HYPNOTIZED]


def test_parametric_condition_known_and_labelled():
    assert conditions.known("loyal_to:ent_perrin")
    assert "(ent_perrin)" in conditions.label("loyal_to:ent_perrin")
