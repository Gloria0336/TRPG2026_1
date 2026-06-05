from app.ai.schemas import EntityExtraction, ItemGrant
from app.db import store
from app.state import game_state


def _count(table: str) -> int:
    return int(store._c().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_register_item_normalizes_and_dedupes():
    first = store.register_item("長劍", category="weapon", slot="main_hand")
    second = store.register_item("一把長劍", category="weapon", slot="main_hand")

    assert first == second
    assert _count("items") == 1


def test_register_item_coerces_invalid_category_to_misc():
    item_id = store.register_item("怪東西", category="legendary_spell_slot")
    item = store.find_item_by_ref(item_id)

    assert item is not None
    assert item["category"] == "misc"


def test_grant_item_lazily_creates_and_merges_quantity():
    one = store.grant_item("pc_bram", "銅鑰匙", quantity=1, category="key_item")
    two = store.grant_item("pc_bram", "銅鑰匙", quantity=2, category="key_item")

    assert one["item_id"] == two["item_id"]
    assert two["quantity"] == 3
    assert _count("items") == 1
    assert _count("actor_inventory") == 1


def test_acquisition_only_mention_does_not_create_inventory():
    parsed = EntityExtraction.model_validate_json(
        '{"deltas":[],"item_grants":[],"location_note":null}'
    )

    assert parsed.actionable() == []
    assert parsed.acquired_items() == []
    assert _count("items") == 0
    assert _count("actor_inventory") == 0


def test_item_grant_schema_filters_category_and_quantity():
    grant = ItemGrant.model_validate({
        "item_name": "藍玻璃戒指",
        "quantity": 0,
        "category": "artifact_of_whatever",
    })

    assert grant.quantity == 1
    assert grant.category is None


def test_get_inventory_supports_pc_and_npc_actor_ids():
    store.grant_item("pc_bram", "長劍", category="weapon")
    store.grant_item("ent_sable", "匕首", category="weapon")

    assert store.get_inventory("pc_bram")[0]["name"] == "長劍"
    assert store.get_inventory("ent_sable")[0]["name"] == "匕首"


def test_new_game_migrates_starting_inventory_and_projection_roundtrips():
    gs = game_state.new_game(channel_id=1)

    bram_inventory = store.get_inventory("pc_bram")
    names = {item["name"] for item in bram_inventory}
    assert {"長劍", "鏈甲", "盾牌"}.issubset(names)
    assert gs.characters["pc_bram"].inventory == store.project_inventory("pc_bram")

    restored = game_state.GameState.from_dict(gs.to_dict())
    assert restored.characters["pc_bram"].inventory == store.project_inventory("pc_bram")
