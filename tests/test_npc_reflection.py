from app.ai import orchestrator, prompts
from app.ai.schemas import EntityExtraction, EntityStateDelta
from app.config import settings
from app.db import store
from app.discord_bot import bot
from app.engine.types import Event, ResolutionResult, ResultKind
from app.state import game_state


def _seed_npc(gs, *, ent_id: str = "ent_barkeep", name: str = "酒保瑪蒂娜") -> str:
    store.upsert_entity(
        id=ent_id,
        scene_id=gs.current_location_id,
        kind="person",
        name=name,
        aliases=["瑪蒂娜", "酒保"],
        status="present",
        disposition="neutral",
        notes="在吧台後觀察客人的外地人。",
    )
    return ent_id


def test_entity_reflections_persist_dedupe_cap_and_reinject():
    gs = game_state.reset_state(channel_id=1)
    ent_id = _seed_npc(gs)

    assert store.append_entity_reflection(ent_id, "她覺得玩家會利用混亂逼近目標。") is True
    assert store.append_entity_reflection(ent_id, "她覺得玩家會利用混亂逼近目標。") is False
    for idx in range(7):
        store.append_entity_reflection(ent_id, f"印象{idx}")

    ent = store.get_entity_by_id(ent_id)
    reflections = store.entity_reflections(ent)
    assert len(reflections) == 6
    assert reflections == [f"印象{idx}" for idx in range(1, 7)]

    result = ResolutionResult(
        kind=ResultKind.NARRATIVE,
        actor_id="pc_bram",
        actor_name="Bram Ironwood",
        summary="Bram talks to the barkeep.",
        target_name="酒保瑪蒂娜",
    )
    ctx = prompts.narrate_context(gs, result)
    assert "對玩家的既有印象" in ctx
    assert "印象6" in ctx


async def test_update_npc_reflection_offline_returns_none_without_write(monkeypatch):
    monkeypatch.setattr(settings, "npc_reflection_enabled", True)
    monkeypatch.setattr(settings, "ai_offline", True)
    monkeypatch.setattr(settings, "openrouter_api_key", "")

    gs = game_state.reset_state(channel_id=1)
    ent_id = _seed_npc(gs)

    assert await orchestrator.update_npc_reflection(gs, ent_id) is None
    assert store.entity_reflections(store.get_entity_by_id(ent_id)) == []


async def test_update_npc_reflection_parses_json_and_writes(monkeypatch):
    async def fake_chat(model, system, user, **kwargs):
        assert model == settings.model_extract
        assert "Return ONLY JSON" in system
        assert "酒保瑪蒂娜" in user
        assert kwargs["json_mode"] is True
        return '{"reflection":"她認定玩家擅長用社交壓力製造破口，往後會先保留退路。"}'

    monkeypatch.setattr(settings, "npc_reflection_enabled", True)
    monkeypatch.setattr(settings, "ai_offline", False)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(orchestrator, "_chat", fake_chat)

    gs = game_state.reset_state(channel_id=1)
    ent_id = _seed_npc(gs)
    gs.add_event(Event(
        actor_id="pc_bram",
        actor_name="Bram Ironwood",
        kind="check",
        summary="Bram pressures 酒保瑪蒂娜 for a name.",
        narration="布拉姆靠近吧台，讓瑪蒂娜不得不壓低聲音回答。",
        data={"target_name": "酒保瑪蒂娜", "raw_text": "我逼問酒保"},
    ))

    reflection = await orchestrator.update_npc_reflection(gs, "瑪蒂娜")

    assert reflection == "她認定玩家擅長用社交壓力製造破口，往後會先保留退路。"
    assert store.entity_reflections(store.get_entity_by_id(ent_id)) == [reflection]


async def test_apply_entity_updates_triggers_reflection_for_significant_delta(monkeypatch):
    async def fake_extract(*args, **kwargs):
        return EntityExtraction(deltas=[
            EntityStateDelta(
                entity_ref="酒保瑪蒂娜",
                disposition="wary",
                commitment="答應在午夜前替玩家留意後門動靜",
            )
        ])

    calls: list[str] = []

    async def fake_update(gs_arg, entity_ref):
        calls.append(entity_ref)
        return "她會更謹慎。"

    monkeypatch.setattr(settings, "npc_reflection_enabled", True)
    monkeypatch.setattr(settings, "npc_reflection_every", 1)
    monkeypatch.setattr(settings, "rolling_summary_enabled", False)
    monkeypatch.setattr(bot.orchestrator, "extract_entity_states", fake_extract)
    monkeypatch.setattr(bot.orchestrator, "update_npc_reflection", fake_update)

    gs = game_state.reset_state(channel_id=1)
    ent_id = _seed_npc(gs)
    result = ResolutionResult(
        kind=ResultKind.CHECK,
        actor_id="pc_bram",
        actor_name="Bram Ironwood",
        summary="Diplomacy check: success",
        target_name="酒保瑪蒂娜",
    )

    await bot._apply_entity_updates(gs, "The barkeep becomes guarded but agrees.", result)

    assert calls == [ent_id]
    ent = store.get_entity_by_id(ent_id)
    assert ent["disposition"] == "wary"
    assert store.entity_commitments(ent) == ["答應在午夜前替玩家留意後門動靜"]


async def test_apply_entity_updates_skips_reflection_when_not_significant(monkeypatch):
    async def fake_extract(*args, **kwargs):
        return EntityExtraction(deltas=[
            EntityStateDelta(entity_ref="酒保瑪蒂娜", note="擦亮一只舊木杯。")
        ])

    calls: list[str] = []

    async def fake_update(gs_arg, entity_ref):
        calls.append(entity_ref)
        return "should not happen"

    monkeypatch.setattr(settings, "npc_reflection_enabled", True)
    monkeypatch.setattr(settings, "npc_reflection_every", 1)
    monkeypatch.setattr(settings, "rolling_summary_enabled", False)
    monkeypatch.setattr(bot.orchestrator, "extract_entity_states", fake_extract)
    monkeypatch.setattr(bot.orchestrator, "update_npc_reflection", fake_update)

    gs = game_state.reset_state(channel_id=1)
    _seed_npc(gs)

    await bot._apply_entity_updates(gs, "The barkeep quietly polishes a cup.", None)

    assert calls == []


async def test_apply_entity_updates_triggers_reflection_for_targeted_check(monkeypatch):
    async def fake_extract(*args, **kwargs):
        return EntityExtraction()

    calls: list[str] = []

    async def fake_update(gs_arg, entity_ref):
        calls.append(entity_ref)
        return "她記住這次施壓。"

    monkeypatch.setattr(settings, "npc_reflection_enabled", True)
    monkeypatch.setattr(settings, "npc_reflection_every", 1)
    monkeypatch.setattr(settings, "rolling_summary_enabled", False)
    monkeypatch.setattr(bot.orchestrator, "extract_entity_states", fake_extract)
    monkeypatch.setattr(bot.orchestrator, "update_npc_reflection", fake_update)

    gs = game_state.reset_state(channel_id=1)
    ent_id = _seed_npc(gs)
    result = ResolutionResult(
        kind=ResultKind.CHECK,
        actor_id="pc_bram",
        actor_name="Bram Ironwood",
        summary="Intimidation check: failure",
        target_name="瑪蒂娜",
    )

    await bot._apply_entity_updates(gs, "The barkeep does not yield.", result)

    assert calls == [ent_id]
