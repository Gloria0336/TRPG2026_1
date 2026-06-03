"""Entity registry + dynamic scene summary — the hooded-figure continuity fix.

These prove the root-cause fix: once a narrative entity is marked departed it stops
being 'present', drops out of the dynamic summary, and the narrator's context lists it
as NO LONGER PRESENT — so it cannot silently reappear 7-8 beats later.
"""
from app.ai import prompts
from app.db import store
from app.engine.types import ResolutionResult, ResultKind
from app.state import game_state


TAVERN_DEFS = [
    {"id": "ent_perrin", "kind": "person", "name": "老佩林",
     "aliases": ["佩林", "商人"], "status": "present", "disposition": "friendly"},
    {"id": "ent_hooded", "kind": "person", "name": "緊張的兜帽客",
     "aliases": ["兜帽客", "兜帽人"], "status": "present", "disposition": "afraid"},
]


def test_seed_then_present():
    store.seed_entities("tavern", TAVERN_DEFS)
    names = {e["name"] for e in store.get_present("tavern")}
    assert names == {"老佩林", "緊張的兜帽客"}


def test_seed_is_idempotent_and_preserves_runtime_state():
    store.seed_entities("tavern", TAVERN_DEFS)
    store.apply_delta("tavern", {"entity_ref": "兜帽客", "status": "departed"})
    store.seed_entities("tavern", TAVERN_DEFS)  # re-seed must not resurrect it
    names = {e["name"] for e in store.get_present("tavern")}
    assert "緊張的兜帽客" not in names


def test_find_by_alias():
    store.seed_entities("tavern", TAVERN_DEFS)
    ent = store.find_by_ref("tavern", "兜帽客")
    assert ent is not None and ent["id"] == "ent_hooded"


def test_apply_delta_departed_removes_from_present_but_kept_in_all():
    store.seed_entities("tavern", TAVERN_DEFS)
    store.apply_delta("tavern", {"entity_ref": "兜帽客", "status": "departed"})
    assert "緊張的兜帽客" not in {e["name"] for e in store.get_present("tavern")}
    assert "緊張的兜帽客" in {e["name"] for e in store.get_all("tavern")}


def test_apply_delta_registers_new_entity():
    store.seed_entities("tavern", TAVERN_DEFS)
    ent_id = store.apply_delta("tavern", {
        "register_kind": "person", "register_name": "酒保",
        "aliases": ["店主"], "disposition": "neutral",
    })
    assert ent_id is not None
    assert "酒保" in {e["name"] for e in store.get_present("tavern")}


def test_invalid_status_is_ignored():
    store.seed_entities("tavern", TAVERN_DEFS)
    # A bogus status must not flip the entity out of 'present'.
    store.apply_delta("tavern", {"entity_ref": "兜帽客", "status": "teleported"})
    assert "緊張的兜帽客" in {e["name"] for e in store.get_present("tavern")}


def test_apply_delta_validates_location_id(caplog):
    """Step 1: location writes go through the global registry. A known location id is
    accepted; a hallucinated one is dropped (the rest of the delta still applies) so an
    entity can't be stranded at a place that does not exist."""
    store.seed_entities("tavern", TAVERN_DEFS)
    store.register_location("東路", location_id="east_road")

    store.apply_delta("tavern", {"entity_ref": "兜帽客", "location_id": "east_road"})
    assert store.find_by_ref("tavern", "兜帽客")["location_id"] == "east_road"

    store.apply_delta("tavern", {"entity_ref": "兜帽客",
                                 "location_id": "no_such_place", "note": "溜走了"})
    ent = store.find_by_ref("tavern", "兜帽客")
    assert ent["location_id"] == "east_road"   # unknown id dropped, not written
    assert "溜走了" in ent["notes"]            # rest of the delta still applied


def test_compose_summary_and_context_reflect_departure():
    gs = game_state.reset_state(channel_id=0)
    assert gs.scene.id == "tavern"
    assert "緊張的兜帽客" in {e["name"] for e in gs.present_entities()}

    store.apply_delta("tavern", {"entity_ref": "兜帽客", "status": "departed"})

    assert "緊張的兜帽客" not in {e["name"] for e in gs.present_entities()}

    summary = prompts.compose_scene_summary(gs)
    assert "已不在場" in summary  # explicit do-not-reintroduce marker

    result = ResolutionResult(
        kind=ResultKind.CHECK, actor_id="pc_bram", actor_name="Bram Ironwood",
        summary="Perception check vs DC 15: FAILURE",
    )
    ctx = prompts.narrate_context(gs, result)
    assert "NO LONGER PRESENT" in ctx
    # The hooded figure is present-listed nowhere; 老佩林 still is.
    assert "HERE NOW" in ctx
    assert "老佩林" in ctx
