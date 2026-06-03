"""Step 1 — PCs are never narrative entities.

A player character's name must never be mention-tallied into, or registered as, an NPC
entity (trace bug: the extractor emitted register_kind=person for the PC's localized
name '布蘭姆·鐵木' and it began counting toward promotion). The guard matches both the
English Character.name and its canonical localized form, and the narrator/extractor are
fed the canonical localized name so narration stops drifting (布蘭姆 → 布拉姆).
"""
import asyncio

from app.ai.schemas import EntityExtraction, EntityStateDelta
from app.db import store
from app.discord_bot import bot
from app.state import game_state


def test_is_pc_ref_matches_english_and_localized():
    gs = game_state.reset_state(channel_id=0)
    assert bot._is_pc_ref(gs, "Bram Ironwood") is True
    assert bot._is_pc_ref(gs, "Bram") is True
    assert bot._is_pc_ref(gs, "布拉姆·鐵木") is True       # canonical localized form
    # Narrative NPCs are DB entities, not Characters → never PCs. Unknown/empty → False.
    assert bot._is_pc_ref(gs, "老佩林") is False
    assert bot._is_pc_ref(gs, "") is False
    assert bot._is_pc_ref(gs, None) is False


def test_apply_entity_updates_skips_pc_register(monkeypatch):
    gs = game_state.reset_state(channel_id=0)
    scope = gs.current_location_id
    # Promote on the first mention so the test fails loudly if the PC is NOT skipped.
    monkeypatch.setattr(bot.settings, "mention_promote_threshold", 1)

    async def fake_extract(state, prose, result):
        return EntityExtraction(deltas=[
            EntityStateDelta(entity_ref="布拉姆·鐵木", register_kind="person",
                             register_name="布拉姆·鐵木", status="present"),
        ])

    monkeypatch.setattr(bot.orchestrator, "extract_entity_states", fake_extract)

    asyncio.run(bot._apply_entity_updates(gs, "布拉姆·鐵木 走回酒館。", None))
    assert "布拉姆·鐵木" not in {e["name"] for e in store.get_all(scope)}


def test_apply_entity_updates_still_registers_real_npc(monkeypatch):
    """The PC guard must be PC-specific, not a blanket block on registration."""
    gs = game_state.reset_state(channel_id=0)
    scope = gs.current_location_id
    monkeypatch.setattr(bot.settings, "mention_promote_threshold", 1)

    async def fake_extract(state, prose, result):
        return EntityExtraction(deltas=[
            EntityStateDelta(register_kind="person", register_name="酒保", status="present"),
        ])

    monkeypatch.setattr(bot.orchestrator, "extract_entity_states", fake_extract)

    asyncio.run(bot._apply_entity_updates(gs, "酒保端上一杯酒。", None))
    assert "酒保" in {e["name"] for e in store.get_all(scope)}
