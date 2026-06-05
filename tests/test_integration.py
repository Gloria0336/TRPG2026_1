"""Headless integration: drive a full combat through the engine the way the bot does,
exercising main actions, the bonus-action economy, downed-PC death saves, and termination.
"""
import pytest

from app.content import scenario
from app.engine import combat
from app.engine.combat import CombatError
from app.engine.types import ActionCost, ActionType
from app.state import game_state


def _start_combat():
    gs = game_state.new_game(channel_id=1)
    # warren carries the encounter now (scenes are projected from locations; ambush removed)
    gs.goto_scene(scenario.scene_by_id("warren"))
    gs.start_scene_combat()
    return gs


def _seek_living_pc_turn(gs, limit=30):
    for _ in range(limit):
        actor = combat.current_combatant(gs)
        if actor and actor.is_pc and not actor.is_down:
            return actor
        combat.run_monster_turn(gs) if actor and not actor.is_pc else None
        combat.advance_turn(gs)
    return None


def test_bonus_action_after_main_action():
    gs = _start_combat()
    pc = _seek_living_pc_turn(gs)
    assert pc is not None

    main = next(a for a in pc.actions if a.cost is ActionCost.ACTION
                and a.type in (ActionType.MELEE_ATTACK, ActionType.RANGED_ATTACK,
                               ActionType.SAVE_SPELL, ActionType.AUTO_HIT))
    target = combat.living_enemies(gs, pc)[0]
    combat.take_action(gs, pc.id, main.name, target.id)
    assert gs.combat.action_used
    assert not gs.combat.bonus_used

    bonus = next(a for a in pc.actions if a.cost is ActionCost.BONUS and (a.uses is None or a.uses > 0))
    combat.take_action(gs, pc.id, bonus.name, None)  # bonus heal on most-hurt ally
    assert gs.combat.bonus_used

    # A second ACTION-cost action is now blocked.
    with pytest.raises(CombatError):
        combat.take_action(gs, pc.id, main.name, target.id)


def test_full_combat_terminates():
    gs = _start_combat()
    safety = 0
    while gs.combat and gs.combat.active and safety < 300:
        safety += 1
        actor = combat.current_combatant(gs)
        if actor is None:
            break
        if actor.is_pc and not actor.is_down:
            enemies = combat.living_enemies(gs, actor)
            if enemies:
                main = actor.actions[0]
                combat.take_action(gs, actor.id, main.name, enemies[0].id)
            combat.advance_turn(gs)
        elif actor.is_pc and actor.is_down and not actor.is_dead:
            combat.take_death_save(gs, actor.id)
            combat.advance_turn(gs)
        else:
            combat.run_monster_turn(gs)
            combat.advance_turn(gs)

    assert gs.combat.outcome in ("victory", "defeat")
    assert not gs.combat.active
    assert len(gs.event_log) > 3


def test_snapshot_during_combat_roundtrips():
    gs = _start_combat()
    _seek_living_pc_turn(gs)
    d = gs.to_dict()
    gs2 = game_state.GameState.from_dict(d)
    assert gs2.combat is not None
    assert len(gs2.combat.order) == len(gs.combat.order)
    assert gs2.combat.current_id == gs.combat.current_id
