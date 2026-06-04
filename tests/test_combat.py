import pytest

from app.ai.schemas import EntityStateDelta
from app.content import monsters
from app.db import store
from app.engine import combat
from app.engine.combat import CombatError
from app.engine import rules_5e
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


def test_archetype_for_entity_uses_kind_and_notes():
    assert monsters.archetype_for_entity({"kind": "creature", "name": "灰狼"}) == "beast"
    assert monsters.archetype_for_entity({"kind": "person", "name": "城門衛兵"}) == "guard"
    assert monsters.archetype_for_entity({"kind": "person", "name": "碼頭打手"}) == "thug"
    assert monsters.archetype_for_entity({"kind": "person", "name": "路人"}) == "commoner"


def test_spawn_from_entity_preserves_entity_id_and_can_fight():
    gs = _fresh()
    ent = {
        "id": "ent_guard",
        "kind": "person",
        "name": "北門衛兵",
        "aliases": ["衛兵"],
        "notes": "受過訓練的守衛",
    }
    npc = monsters.spawn_from_entity(ent)
    gs.characters[npc.id] = npc
    gs.characters["target_dummy"] = monsters.spawn_from_entity({
        "id": "target_dummy",
        "kind": "person",
        "name": "碼頭打手",
        "notes": "惡棍",
    })
    gs.combat = combat.start_combat(gs, ["ent_guard", "target_dummy"])
    gs.combat.order = [("ent_guard", 20), ("target_dummy", 10)]
    result = combat.take_action(gs, "ent_guard", npc.actions[0].name, "target_dummy")
    assert npc.id == "ent_guard"
    assert result.actor_id == "ent_guard"
    assert result.target_id == "target_dummy"


def test_start_entity_combat_includes_hostiles_and_trigger_target():
    gs = _fresh()
    store.upsert_entity(
        id="ent_hostile", scene_id=gs.current_location_id, kind="person",
        name="怒目傭兵", status="present", disposition="hostile",
    )
    store.upsert_entity(
        id="ent_neutral", scene_id=gs.current_location_id, kind="person",
        name="旁觀旅人", status="present", disposition="neutral",
    )
    store.upsert_entity(
        id="ent_friendly", scene_id=gs.current_location_id, kind="person",
        name="友善酒保", status="present", disposition="friendly",
    )

    state = gs.start_entity_combat(trigger_entity_id="ent_neutral")

    assert state is not None
    ids = {cid for cid, _init in state.order}
    assert {"ent_hostile", "ent_neutral"}.issubset(ids)
    assert "ent_friendly" not in ids
    assert store.get_entity_by_id("ent_neutral")["disposition"] == "hostile"


def test_escalate_hostiles_promotes_on_fourth_freeplay_round():
    gs = _fresh()
    store.upsert_entity(
        id="ent_thug", scene_id=gs.current_location_id, kind="person",
        name="碼頭打手", status="present", disposition="hostile",
    )

    gs.flags["freeplay_round"] = 1
    assert gs.escalate_hostiles() == []
    assert store.get_entity_by_id("ent_thug")["flags"]["hostile_since_round"] == 1

    for round_no in (2, 3):
        gs.flags["freeplay_round"] = round_no
        assert gs.escalate_hostiles() == []
        assert store.get_entity_by_id("ent_thug")["disposition"] == "hostile"

    gs.flags["freeplay_round"] = 4
    assert gs.escalate_hostiles() == ["ent_thug"]
    saved = store.get_entity_by_id("ent_thug")
    assert saved["disposition"] == "attack"
    assert "hostile_since_round" not in saved["flags"]


def test_attack_disposition_validates_through_schema_and_store():
    gs = _fresh()
    store.upsert_entity(
        id="ent_guard", scene_id=gs.current_location_id, kind="person",
        name="北門衛兵", status="present", disposition="neutral",
    )

    delta = EntityStateDelta(entity_ref="北門衛兵", disposition="attack")
    assert delta.disposition == "attack"
    store.apply_delta(gs.current_location_id, delta.model_dump(exclude_none=True))
    assert store.get_entity_by_id("ent_guard")["disposition"] == "attack"


def test_reconcile_combat_entities_writes_dead_and_cowed():
    gs = _fresh()
    for ent_id, name in (("ent_dead", "瀕死打手"), ("ent_survivor", "投降守衛")):
        store.upsert_entity(
            id=ent_id, scene_id=gs.current_location_id, kind="person",
            name=name, status="present", disposition="hostile",
        )

    gs.start_entity_combat()
    rules_5e.apply_damage(gs.characters["ent_dead"], 999)
    gs.combat.active = False
    gs.combat.outcome = "victory"

    touched = gs.reconcile_combat_entities()

    assert {"ent_dead", "ent_survivor"}.issubset(set(touched))
    assert store.get_entity_by_id("ent_dead")["status"] == "dead"
    assert store.get_entity_by_id("ent_survivor")["disposition"] == "cowed"
