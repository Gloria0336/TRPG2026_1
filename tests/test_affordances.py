import asyncio

from app.ai import prompts
from app.ai import orchestrator
from app.ai.schemas import AffordanceCard, EntityExtraction, EntityStateDelta
from app.config import settings
from app.content import affordances
from app.db import store
from app.discord_bot import bot
from app.engine import conditions as cond
from app.state import game_state


def test_lookup_normalizes_interactive_feature_phrase():
    card = affordances.lookup("可翻找的貨車與散箱")

    assert card is not None
    assert "search" in card["can_be"]
    assert "provide cover" in card["effects"]


def test_lookup_accepts_alias_and_returns_copy():
    first = affordances.lookup("oil lamp")
    second = affordances.lookup("oil lamp")

    assert first is not None and second is not None
    first["effects"].append("mutated")
    assert "mutated" not in second["effects"]


def test_effects_to_conditions_filters_known_condition_ids():
    assert affordances.effects_to_conditions("wrap cloth to restrain the target") == [cond.RESTRAINED]
    assert affordances.effects_to_conditions("unknown sparkling nonsense") == []


def test_intent_context_includes_location_card_affordances(monkeypatch):
    monkeypatch.setattr(settings, "affordances_enabled", True)
    gs = game_state.reset_state(channel_id=1)
    store.upsert_location_card(
        location_id=gs.current_location_id,
        canonical_name="晨橋路邊酒館",
        interactive_features=["可請人喝酒攀談的吧台", "可翻找的貨車與散箱"],
        generated_source="test",
    )

    ctx = prompts.intent_context(gs, gs.pcs()[0], "我把酒灑在吧台旁邊")

    assert "INTERACTIVE OBJECT AFFORDANCES" in ctx
    assert "可請人喝酒攀談的吧台" in ctx
    assert "can_be=" in ctx
    assert "effects=" in ctx
    assert "safe_condition_ids=" not in ctx or cond.RESTRAINED in ctx or cond.NPC_DISTRACTED in ctx


def test_intent_context_includes_present_object_affordances(monkeypatch):
    monkeypatch.setattr(settings, "affordances_enabled", True)
    gs = game_state.reset_state(channel_id=1)
    store.upsert_entity(
        id="obj_test_lamp",
        scene_id=gs.current_location_id,
        kind="object",
        name="油燈",
        aliases=["oil lamp"],
    )

    ctx = prompts.intent_context(gs, gs.pcs()[0], "我打翻油燈")

    assert "INTERACTIVE OBJECT AFFORDANCES" in ctx
    assert "油燈" in ctx
    assert "flammable" in ctx
    assert "ignite" in ctx


def test_affordances_flag_disables_prompt_block(monkeypatch):
    monkeypatch.setattr(settings, "affordances_enabled", False)
    gs = game_state.reset_state(channel_id=1)
    store.upsert_location_card(
        location_id=gs.current_location_id,
        canonical_name="晨橋路邊酒館",
        interactive_features=["可翻找的貨車與散箱"],
        generated_source="test",
    )

    ctx = prompts.intent_context(gs, gs.pcs()[0], "我翻找貨車")

    assert "INTERACTIVE OBJECT AFFORDANCES" not in ctx


async def test_build_affordance_card_uses_static_fallback(monkeypatch):
    monkeypatch.setattr(settings, "ai_offline", True)

    card, state = await orchestrator.build_affordance_card({"name": "oil lamp"})

    assert state == "fallback"
    assert "ignite" in card.effects
    assert "flammable" in card.tags


async def test_build_affordance_card_parses_ai_json(monkeypatch):
    async def fake_chat(*args, **kwargs):
        return (
            '{"material":["glass"],"can_be":["reflect light"],'
            '"effects":["draw attention"],"tags":["fragile"]}'
        )

    monkeypatch.setattr(settings, "ai_offline", False)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(orchestrator, "_chat", fake_chat)

    card, state = await orchestrator.build_affordance_card({"name": "mirror shard"})

    assert state == "ready"
    assert card.material == ["glass"]
    assert card.tags == ["fragile"]


async def test_object_promotion_caches_generated_affordance(monkeypatch):
    async def fake_extract(*args, **kwargs):
        return EntityExtraction(
            deltas=[
                EntityStateDelta(
                    register_kind="object",
                    register_name="mirror shard",
                    aliases=["silvered shard"],
                    status="present",
                )
            ]
        )

    async def fake_build(request):
        return (
            AffordanceCard(
                material=["glass"],
                can_be=["reflect light"],
                effects=["draw attention"],
                tags=["fragile"],
            ),
            "ready",
        )

    monkeypatch.setattr(settings, "affordance_generation_enabled", True)
    monkeypatch.setattr(settings, "mention_promote_threshold", 1)
    monkeypatch.setattr(settings, "rolling_summary_enabled", False)
    monkeypatch.setattr(bot.orchestrator, "extract_entity_states", fake_extract)
    monkeypatch.setattr(bot.orchestrator, "build_affordance_card", fake_build)

    gs = game_state.reset_state(channel_id=1)
    await bot._apply_entity_updates(gs, "A mirror shard glints under the table.", None)
    await asyncio.sleep(0)

    ent = store.find_by_ref(gs.current_location_id, "mirror shard")
    assert ent is not None
    assert ent["flags"]["affordance"]["material"] == ["glass"]
