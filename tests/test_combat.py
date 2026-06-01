import pytest

from app.engine import combat
from app.engine.combat import CombatError
from app.state import game_state


def _fresh():
    return game_state.new_game(channel_id=1)


def test_start_combat_builds_order():
    gs = _fresh()
    # ambush scene has the goblin encounter; jump there
    from app.content import scenario
    gs.goto_scene(scenario.scene_by_id("ambush"))
    c = gs.start_scene_combat()
    assert c is not None
    assert len(c.order) == 2 + 3  # 2 PCs + 3 goblins
    assert c.round == 1
    # order sorted by initiative desc
    inits = [i for _, i in c.order]
    assert inits == sorted(inits, reverse=True)


def test_turn_advance_increments_round():
    gs = _fresh()
    from app.content import scenario
    gs.goto_scene(scenario.scene_by_id("ambush"))
    c = gs.start_scene_combat()
    n = len(c.order)
    for _ in range(n):
        combat.advance_turn(gs)
    assert gs.combat.round == 2


def test_action_economy_blocks_second_action():
    gs = _fresh()
    from app.content import scenario
    gs.goto_scene(scenario.scene_by_id("ambush"))
    gs.start_scene_combat()
    # Fast-forward to a PC's turn.
    guard = 0
    while not (combat.current_combatant(gs) and combat.current_combatant(gs).is_pc):
        combat.run_monster_turn(gs)
        combat.advance_turn(gs)
        guard += 1
        assert guard < 20
    pc = combat.current_combatant(gs)
    sword = pc.find_action(pc.actions[0].name)
    combat.take_action(gs, pc.id, sword.name, target_ref="goblin_1")
    with pytest.raises(CombatError):
        # second ACTION-cost action same turn → blocked
        combat.take_action(gs, pc.id, sword.name, target_ref="goblin_1")


def test_wrong_turn_rejected():
    gs = _fresh()
    from app.content import scenario
    gs.goto_scene(scenario.scene_by_id("ambush"))
    gs.start_scene_combat()
    cur = combat.current_combatant(gs)
    other = next(c for c in gs.pcs() if c.id != cur.id) if cur.is_pc else gs.pcs()[0]
    with pytest.raises(CombatError):
        combat.take_action(gs, other.id, other.actions[0].name, target_ref="goblin_1")


def test_victory_detection():
    gs = _fresh()
    from app.content import scenario
    from app.engine import rules_5e
    gs.goto_scene(scenario.scene_by_id("ambush"))
    gs.start_scene_combat()
    for c in gs.characters.values():
        if not c.is_pc:
            rules_5e.apply_damage(c, 999)
    assert combat.check_combat_end(gs) == "victory"


def test_monster_turn_runs():
    gs = _fresh()
    from app.content import scenario
    gs.goto_scene(scenario.scene_by_id("ambush"))
    gs.start_scene_combat()
    # Walk to a monster's turn and run it.
    guard = 0
    while combat.current_combatant(gs).is_pc:
        combat.advance_turn(gs)
        guard += 1
        assert guard < 10
    results = combat.run_monster_turn(gs)
    assert isinstance(results, list)
