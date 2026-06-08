import pytest

from app.ai import orchestrator, prompts
from app.ai.schemas import DCAssessment, FictionalPosition
from app.config import settings
from app.db import store
from app.engine import resolution
from app.engine.types import (
    BoonType,
    CostType,
    Intent,
    IntentTier,
    ResolutionResult,
    ResultBand,
    ResultKind,
)
from app.state import game_state


def _fresh():
    return game_state.new_game(channel_id=1)


def _creative_intent() -> Intent:
    return Intent(
        actor_id="pc_bram",
        raw_text="spill wine, fake a stumble, and steal the keys",
        tier=IntentTier.A,
        action="steal",
        approach="thievery",
        target="guard keys",
        goal="get the keys without open violence",
        steps=["spill wine", "fake a stumble", "steal the keys"],
        feasibility="low",
        side_effects=["floor becomes slick", "guard is distracted"],
    )


def test_fictional_position_schema_clamps_invalid_hints():
    p = FictionalPosition.model_validate({
        "advantage": True,
        "disadvantage": False,
        "cost_hint": "made_up_cost",
        "boon_hint": "also_fake",
        "outcome_scaffold": {
            "full": "clean opening",
            "partial": "messy progress",
            "fail": "the distraction backfires",
            "extra": "drop me",
        },
    })

    assert p.advantage is True
    assert p.cost_hint is None
    assert p.boon_hint is None
    assert p.outcome_scaffold == {
        "full": "clean opening",
        "partial": "messy progress",
        "fail": "the distraction backfires",
    }


def test_creative_resolver_context_includes_affordances(monkeypatch):
    monkeypatch.setattr(settings, "affordances_enabled", True)
    gs = _fresh()
    store.upsert_location_card(
        location_id=gs.current_location_id,
        canonical_name="晨橋路邊酒館",
        interactive_features=["可請人喝酒攀談的吧台"],
        generated_source="test",
    )
    ctx = prompts.creative_resolver_context(gs, _creative_intent())

    assert "PLAYER INTENT" in ctx
    assert "steps: spill wine, fake a stumble, steal the keys" in ctx
    assert "INTERACTIVE OBJECT AFFORDANCES" in ctx
    assert "do not decide success/failure or any number" in ctx


@pytest.mark.asyncio
async def test_creative_resolver_returns_none_when_offline(monkeypatch):
    monkeypatch.setattr(settings, "creative_resolver_enabled", True)
    monkeypatch.setattr(settings, "ai_offline", True)
    gs = _fresh()

    assert await orchestrator.resolve_creative_position(gs, _creative_intent()) is None


@pytest.mark.asyncio
async def test_creative_resolver_parses_valid_json(monkeypatch):
    async def fake_chat(*args, **kwargs):
        return (
            '{"advantage":true,"disadvantage":false,"cost_hint":"attention",'
            '"boon_hint":"opening","outcome_scaffold":{"full":"clean opening",'
            '"partial":"messy progress","fail":"the guard notices"},"rationale":"uses cover"}'
        )

    monkeypatch.setattr(settings, "creative_resolver_enabled", True)
    monkeypatch.setattr(settings, "ai_offline", False)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(orchestrator, "_chat", fake_chat)

    parsed = await orchestrator.resolve_creative_position(_fresh(), _creative_intent())

    assert parsed is not None
    assert parsed.advantage is True
    assert parsed.cost_hint is CostType.ATTENTION
    assert parsed.boon_hint is BoonType.OPENING


def test_resolve_consumes_position_without_changing_dc(monkeypatch):
    captured = {}

    def fake_check(actor, skill, dc, *, advantage=False, disadvantage=False, external_bonus=0):
        captured.update({
            "dc": dc,
            "advantage": advantage,
            "disadvantage": disadvantage,
            "external_bonus": external_bonus,
        })
        return ResolutionResult(
            kind=ResultKind.CHECK,
            actor_id=actor.id,
            actor_name=actor.name,
            success=False,
            band=ResultBand.FAILURE,
            dc=dc,
            summary=f"{skill} check vs DC {dc}: FAILURE",
            narration_hint="Describe a setback; the attempt fails and a cost lands.",
        )

    monkeypatch.setattr(resolution.rules_5e, "ability_check", fake_check)
    gs = _fresh()
    position = FictionalPosition(
        advantage=True,
        cost_hint=CostType.ATTENTION,
        outcome_scaffold={"fail": "the staged stumble draws eyes to the hand"},
    )
    assessment = DCAssessment(base_dc=10, env_modifier=0, final_dc=10)

    result = resolution.resolve(gs, _creative_intent(), assessment=assessment, position=position)

    assert captured["dc"] == 10
    assert captured["advantage"] is True
    assert result.dc == 10
    assert result.cost is not None
    assert result.cost.type is CostType.ATTENTION
    assert "Creative scaffold: the staged stumble draws eyes to the hand" in result.narration_hint


def test_pick_boon_accepts_preferred_type():
    gs = _fresh()

    boon = resolution.pick_boon(gs, "diplomacy", ResultBand.CRIT_SUCCESS, BoonType.OPENING)

    assert boon is not None
    assert boon.type is BoonType.OPENING
