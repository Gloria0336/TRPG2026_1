import pytest

from app.ai.schemas import EntityStateDelta
from app.content import monsters
from app.db import store
from app.engine import combat
from app.engine.combat import CombatError
from app.engine import rules_5e
from app.engine.types import Intent, IntentTier
from app.state import game_state


def _fresh():
    return game_state.new_game(channel_id=1)


def test_start_combat_builds_order():
    gs = _fresh()
    # warren now carries the encounter (goblin_boss + goblin); jump there
    from app.content import scenario
    gs.goto_scene(scenario.scene_by_id("warren"))
    c = gs.start_scene_combat()
    assert c is not None
    assert len(c.order) == 2 + 2  # 2 PCs + boss + goblin
    assert c.round == 1
    # order sorted by initiative desc
    inits = [i for _, i in c.order]
    assert inits == sorted(inits, reverse=True)


def test_turn_advance_increments_round():
    gs = _fresh()
    from app.content import scenario
    gs.goto_scene(scenario.scene_by_id("warren"))
    c = gs.start_scene_combat()
    n = len(c.order)
    for _ in range(n):
        combat.advance_turn(gs)
    assert gs.combat.round == 2


def test_action_economy_blocks_second_action():
    gs = _fresh()
    from app.content import scenario
    gs.goto_scene(scenario.scene_by_id("warren"))
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
    gs.goto_scene(scenario.scene_by_id("warren"))
    gs.start_scene_combat()
    cur = combat.current_combatant(gs)
    other = next(c for c in gs.pcs() if c.id != cur.id) if cur.is_pc else gs.pcs()[0]
    with pytest.raises(CombatError):
        combat.take_action(gs, other.id, other.actions[0].name, target_ref="goblin_1")


def test_victory_detection():
    gs = _fresh()
    from app.content import scenario
    from app.engine import rules_5e
    gs.goto_scene(scenario.scene_by_id("warren"))
    gs.start_scene_combat()
    for c in gs.characters.values():
        if not c.is_pc:
            rules_5e.apply_damage(c, 999)
    assert combat.check_combat_end(gs) == "victory"


def test_monster_turn_runs():
    gs = _fresh()
    from app.content import scenario
    gs.goto_scene(scenario.scene_by_id("warren"))
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


# ── combat-trigger materialisation (attack reliably enters combat) ──

def test_infer_combat_kind_person_vs_creature():
    assert monsters.infer_combat_kind("酒保") == "person"
    assert monsters.infer_combat_kind("the bartender") == "person"
    assert monsters.infer_combat_kind("哥布林") == "creature"
    assert monsters.infer_combat_kind("野狼") == "creature"
    # unknown noun defaults to creature (beast archetype)
    assert monsters.infer_combat_kind("某個怪東西") == "creature"


def test_register_combatant_is_immediate_and_idempotent():
    gs = _fresh()
    ent_id = store.register_combatant(
        gs.current_location_id, "哥布林", "creature", disposition="hostile",
    )
    assert ent_id is not None
    ent = store.get_entity_by_id(ent_id)
    assert ent["status"] == "present" and ent["disposition"] == "hostile"
    # second call on the same ref merges onto the existing record, no duplicate
    again = store.register_combatant(gs.current_location_id, "哥布林", "creature")
    assert again == ent_id
    assert len([e for e in store.get_present(gs.current_location_id) if e["name"] == "哥布林"]) == 1


def _attack_intent(target: str, action: str = "攻擊"):
    return Intent(actor_id="pc_bram", raw_text=f"攻擊{target}", tier=IntentTier.A,
                  action=action, target=target, is_attack=True)


def test_resolve_attack_trigger_materialises_unregistered_target():
    from app.discord_bot import bot
    gs = _fresh()
    # 酒保 is only a location-card fixture, not a registered entity
    assert store.find_by_ref(gs.current_location_id, "酒保") is None
    trigger = bot._resolve_attack_trigger(gs, _attack_intent("酒保"))
    assert trigger is not None
    ent = store.get_entity_by_id(trigger)
    assert ent["name"] == "酒保" and ent["kind"] == "person" and ent["status"] == "present"
    # a barkeep materialises FRIENDLY — not an instant enemy (provocation ladder)
    assert ent["disposition"] == "friendly"
    # forcing entity combat directly still works (trigger path flips it hostile)
    state = gs.start_entity_combat(trigger_entity_id=trigger)
    assert state is not None and trigger in {cid for cid, _ in state.order}


def test_resolve_attack_trigger_materialises_malicious_target_as_hostile():
    from app.discord_bot import bot
    gs = _fresh()
    trigger = bot._resolve_attack_trigger(gs, _attack_intent("一名刺客"))
    assert trigger is not None
    assert store.get_entity_by_id(trigger)["disposition"] == "hostile"


def test_resolve_attack_trigger_finds_existing_present_hostile_via_pronoun():
    from app.discord_bot import bot
    gs = _fresh()
    store.register_combatant(gs.current_location_id, "哥布林", "creature", disposition="hostile")
    # a bare pronoun resolves to the lone present hostile, not a new "他" entity
    trigger = bot._resolve_attack_trigger(gs, _attack_intent("他"))
    assert trigger is not None
    assert store.get_entity_by_id(trigger)["name"] == "哥布林"


def test_resolve_attack_trigger_skips_bare_pronoun_with_no_hostile():
    from app.discord_bot import bot
    gs = _fresh()
    assert bot._resolve_attack_trigger(gs, _attack_intent("他")) is None


def test_resolve_attack_trigger_ignores_travel_target():
    from app.discord_bot import bot
    gs = _fresh()
    store.register_location("東路", location_id="east_road")
    # "前往東路" reads as travel, never an attack target → no combatant conjured
    assert bot._resolve_attack_trigger(gs, _attack_intent("東路", action="前往")) is None


# ── provocation ladder (friendly→neutral→hostile; combat only when hostile) ──

def test_infer_disposition_buckets():
    assert monsters.infer_disposition("哥布林") == "hostile"
    assert monsters.infer_disposition("一名刺客") == "hostile"
    assert monsters.infer_disposition("酒保") == "friendly"
    assert monsters.infer_disposition("商人") == "friendly"
    assert monsters.infer_disposition("陌生的旅伴") == "neutral"


def test_provoke_friendly_takes_three_swings_to_reach_combat():
    gs = _fresh()
    ent_id = store.register_combatant(gs.current_location_id, "酒保", "person", disposition="friendly")
    G = game_state.GameState
    # swing 1: friendly → neutral, still not a fight
    assert gs.provoke_entity(ent_id) == "neutral"
    assert not G.is_combat_hostile(store.get_entity_by_id(ent_id)["disposition"])
    # swing 2: neutral → hostile, still not a fight (this swing only flips attitude)
    assert gs.provoke_entity(ent_id) == "hostile"
    assert G.is_combat_hostile(store.get_entity_by_id(ent_id)["disposition"])
    # swing 3 would now route to real combat (dispatch checks is_combat_hostile first)


def test_provoke_neutral_reaches_hostile_in_one_swing():
    gs = _fresh()
    ent_id = store.register_combatant(gs.current_location_id, "旅人", "person", disposition="neutral")
    assert gs.provoke_entity(ent_id) == "hostile"


def test_provoke_records_baseline_for_recovery():
    gs = _fresh()
    ent_id = store.register_combatant(gs.current_location_id, "酒保", "person", disposition="friendly")
    gs.provoke_entity(ent_id)
    flags = store.get_entity_by_id(ent_id)["flags"]
    assert flags["provoke_baseline"] == "friendly"
    assert "provoked_at" in flags


def test_provoke_submissive_unit_does_not_escalate():
    gs = _fresh()
    ent_id = store.register_combatant(gs.current_location_id, "瑟縮的俘虜", "person", disposition="afraid")
    assert gs.provoke_entity(ent_id) == "afraid"      # cowers/flees, never turns to combat
    assert store.get_entity_by_id(ent_id)["disposition"] == "afraid"


def test_recover_provoked_restores_baseline_after_a_day():
    gs = _fresh()
    ent_id = store.register_combatant(gs.current_location_id, "酒保", "person", disposition="friendly")
    gs.provoke_entity(ent_id)
    gs.provoke_entity(ent_id)
    assert store.get_entity_by_id(ent_id)["disposition"] == "hostile"
    # less than a day: no recovery
    gs.advance_minutes(60 * 23)
    assert gs.recover_provoked_dispositions() == []
    assert store.get_entity_by_id(ent_id)["disposition"] == "hostile"
    # cross the one-day mark with no further provocation: attitude resets to baseline
    gs.advance_minutes(60 * 2)
    assert gs.recover_provoked_dispositions() == [ent_id]
    restored = store.get_entity_by_id(ent_id)
    assert restored["disposition"] == "friendly"
    assert "provoke_baseline" not in restored["flags"] and "provoked_at" not in restored["flags"]
