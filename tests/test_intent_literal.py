"""Literal-intent honor — parser must not paternalise weird-but-clear actions.

These prove the trace.log "internal" dead end is closed: a message with a clear
verb + target (even if the topic is silly, taboo, or off-script) must classify
as tier A and carry the specific topic through to the narrator. implausibility
is reserved for world-contradicting actions only."""
from app.ai import prompts
from app.ai.schemas import INTENT_JSON_SHAPE, IntentParse
from app.engine import resolution
from app.engine.types import Intent, IntentTier, ResolutionResult, ResultKind
from app.state import game_state


# ───────────────────────── topic round-trips through the pipeline ─────────────────────────
def test_intent_parse_accepts_topic_field():
    p = IntentParse.model_validate_json(
        '{"tier":"A","action":"詢問","target":"兜帽客","approach":"persuasion",'
        '"topic":"內褲顏色","needs_check":true,"implausible":false}'
    )
    assert p.topic == "內褲顏色"
    assert p.tier == "A"


def test_intent_parse_topic_defaults_to_none():
    p = IntentParse.model_validate_json(
        '{"tier":"A","action":"persuade","target":"老佩林","approach":"persuasion"}'
    )
    assert p.topic is None


def test_intent_dataclass_carries_topic():
    intent = Intent(
        actor_id="pc_bram", raw_text="問兜帽客內褲顏色",
        tier=IntentTier.A, action="詢問", target="兜帽客",
        approach="persuasion", topic="內褲顏色",
    )
    assert intent.topic == "內褲顏色"
    d = intent.to_dict()
    assert d["topic"] == "內褲顏色"


def test_resolve_propagates_topic_to_result():
    gs = game_state.reset_state(channel_id=1)
    intent = Intent(
        actor_id="pc_bram", raw_text="問兜帽客內褲顏色",
        tier=IntentTier.A, action="詢問", target="兜帽客",
        approach="persuasion", topic="內褲顏色",
    )
    result = resolution.resolve(gs, intent, proposed_dc=10)
    assert result.topic == "內褲顏色"


def test_narrate_context_surfaces_topic_literally():
    gs = game_state.reset_state(channel_id=1)
    result = ResolutionResult(
        kind=ResultKind.CHECK, actor_id="pc_bram", actor_name="Bram Ironwood",
        summary="Persuasion check vs DC 13: SUCCESS",
        target_name="兜帽客", raw_text="問兜帽客內褲顏色", topic="內褲顏色",
    )
    ctx = prompts.narrate_context(gs, result)
    # The literal topic must appear with a "do not soften" directive.
    assert "內褲顏色" in ctx
    assert "topic" in ctx.lower()
    assert "do not soften" in ctx.lower() or "do not translate" in ctx.lower()


def test_narrate_context_omits_topic_block_when_none():
    gs = game_state.reset_state(channel_id=1)
    result = ResolutionResult(
        kind=ResultKind.CHECK, actor_id="pc_bram", actor_name="Bram Ironwood",
        summary="Persuasion check vs DC 13: SUCCESS",
        target_name="老佩林", raw_text="說服佩林",
    )
    ctx = prompts.narrate_context(gs, result)
    # When topic is unset we don't emit the "topic:" line at all.
    assert "topic (the specific subject" not in ctx


# ───────────────────────── INTENT_SYSTEM prompt rewrites ─────────────────────────
def test_intent_system_states_player_agency():
    """The new clause makes the parser stop downgrading weird-but-clear actions
    to tier C. Without this, the trace.log underwear case repeats."""
    sys = prompts.INTENT_SYSTEM
    assert "PLAYER AGENCY" in sys or "agency" in sys.lower()
    assert "anti-paternalism" in sys.lower() or "do not substitute" in sys.lower() \
           or "do NOT substitute" in sys


def test_intent_system_lists_silly_examples_as_tier_a():
    """Concrete tier-A examples for taboo/silly/off-topic actions — without these
    the model still infers tier C from generic 'be reasonable' priors."""
    sys = prompts.INTENT_SYSTEM
    # At least one of the canonical examples must be present.
    assert any(ex in sys for ex in ("內褲顏色", "舔門把", "私生活", "穿不穿襪子"))


def test_intent_system_tightens_implausible_with_counter_examples():
    """The implausible clause used to be a vibe check. The rewrite must say
    'world-contradicting only' and list NOT-implausible counter-examples."""
    sys = prompts.INTENT_SYSTEM
    assert "STRICT" in sys or "strict" in sys.lower()
    # Counter-examples section: socially awkward stuff is explicitly NOT implausible.
    assert "NOT implausible" in sys
    # At least one taboo/silly counter-example.
    assert any(ex in sys for ex in ("內褲", "牧師", "舔門把", "性生活"))


def test_intent_json_shape_advertises_topic_slot():
    """The shape doc embedded in the system prompt must list `topic`, otherwise
    the model has no schema clue to fill it."""
    assert "topic" in INTENT_JSON_SHAPE
    assert "specific subject" in INTENT_JSON_SHAPE
