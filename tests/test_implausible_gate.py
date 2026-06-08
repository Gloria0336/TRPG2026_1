"""Step 5 — false-premise (implausibility) gate + actor inventory grounding.

The C4-mine incident: a player conjured explosives they never had and the parser turned
it into a legitimising options menu. Now the parser is given the actor's INVENTORY and an
`implausible` flag, and the bot redirects in-world instead of resolving the false premise.
"""
from app.ai import orchestrator, prompts
from app.ai.schemas import IntentParse
from app.engine.types import Intent, IntentTier
from app.state import game_state


def test_intentparse_accepts_implausible_flag():
    p = IntentParse.model_validate_json(
        '{"tier":"C","action":"enter","target":"礦坑","implausible":true}'
    )
    assert p.implausible is True
    # Defaults false when omitted (backward compatible with old replies).
    p2 = IntentParse.model_validate_json('{"tier":"A","action":"search"}')
    assert p2.implausible is False


def test_to_intent_propagates_implausible():
    p = IntentParse(tier="C", action="enter", target="礦坑", implausible=True)
    intent = orchestrator._to_intent("pc_lyra", "拿出預藏的C4炸藥炸礦坑", p)
    assert isinstance(intent, Intent)
    assert intent.implausible is True
    assert intent.tier is IntentTier.C


def test_impossible_feasibility_derives_implausible():
    p = IntentParse.model_validate_json(
        '{"tier":"A","action":"detonate","target":"C4","feasibility":"impossible",'
        '"implausible":false}'
    )
    assert p.feasibility == "impossible"
    assert p.implausible is True

    intent = orchestrator._to_intent("pc_lyra", "detonate the C4", p)
    assert intent.feasibility == "impossible"
    assert intent.implausible is True


def test_intent_context_exposes_actor_inventory():
    gs = game_state.reset_state(channel_id=0)
    bram = gs.characters["pc_bram"]
    ctx = prompts.intent_context(gs, bram, "拿出預藏的C4炸藥")
    assert "ACTOR INVENTORY" in ctx
    inv_line = next(l for l in ctx.splitlines() if l.startswith("ACTOR INVENTORY"))
    assert "火把數支" in inv_line     # a real carried item is listed
    assert "C4" not in inv_line       # the parser is never told the actor has C4
    assert "炸藥" not in inv_line


def test_pc_has_starting_inventory_and_roundtrips():
    gs = game_state.reset_state(channel_id=0)
    lyra = gs.characters["pc_lyra"]
    assert lyra.inventory and "聖徽" in lyra.inventory
    restored = game_state.GameState.from_dict(gs.to_dict())
    assert restored.characters["pc_lyra"].inventory == lyra.inventory
