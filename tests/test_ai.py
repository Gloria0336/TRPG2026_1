"""AI orchestrator tests — run fully offline (AI_OFFLINE fallbacks).

The key guarantee under test is design §4.0: the AI never touches numbers. Narration is
produced from an engine-computed ResolutionResult and must not change any of its fields.
"""
import copy

import pytest

from app.ai import orchestrator
from app.ai import prompts
from app.ai.schemas import IntentParse
from app.config import settings
from app.engine import rules_5e
from app.engine.types import IntentTier, ResolutionResult, ResultKind
from app.discord_bot import i18n
from app.content.monsters import spawn
from app.content.characters import premade_pcs
from app.state import game_state


@pytest.fixture(autouse=True)
def _force_offline():
    """Run the AI layer in offline mode so tests need no network/key."""
    prev = settings.ai_offline
    settings.ai_offline = True
    yield
    settings.ai_offline = prev


def _fresh():
    return game_state.new_game(channel_id=1)


async def test_offline_intent_attack():
    gs = _fresh()
    intent, dc = await orchestrator.interpret(gs, "pc_bram", "I attack the goblin with my sword")
    assert intent.is_attack
    assert intent.tier is IntentTier.A


async def test_offline_intent_skill():
    gs = _fresh()
    intent, dc = await orchestrator.interpret(gs, "pc_lyra", "I try to persuade Old Perrin")
    assert intent.approach == "persuasion"
    assert intent.tier is IntentTier.A


async def test_offline_intent_vague_is_b_or_c():
    gs = _fresh()
    intent, _ = await orchestrator.interpret(gs, "pc_bram", "hmm uncertain stuff")
    assert intent.tier in (IntentTier.B, IntentTier.C)


async def test_narration_does_not_mutate_numbers():
    gs = _fresh()
    bram, _ = gs.pcs()[0], gs.pcs()[1]
    goblin = spawn("goblin", 1)
    gs.characters[goblin.id] = goblin
    result = rules_5e.attack(bram, goblin, bram.find_action("Longsword"))
    snapshot = copy.deepcopy(result.to_dict())

    prose = await orchestrator.narrate(gs, result)

    assert isinstance(prose, str) and prose
    # Engine-owned numeric fields are untouched by narration.
    assert result.to_dict() == snapshot


async def test_offline_narration_localizes_fumble_hint():
    gs = _fresh()
    result = ResolutionResult(
        kind=ResultKind.CHECK,
        actor_id="pc_bram",
        actor_name="Bram Ironwood",
        success=False,
        fumble=True,
        summary="Perception check vs DC 15: FAILURE (fumble!)",
        narration_hint="Describe an unlucky complication.",
    )

    prose = await orchestrator.narrate(gs, result)

    assert "事情突然變糟" in prose
    assert "Describe an unlucky complication" not in prose


def test_i18n_does_not_replace_word_fragments():
    translated = i18n.text("Perception check vs DC 15: FAILURE. Describe an unlucky complication.")

    assert "察覺" in translated
    assert "complicati目標" not in translated
    assert "Percepti目標" not in translated


async def test_offline_choice_options_display_in_chinese():
    gs = _fresh()
    intent_b, _ = await orchestrator.interpret(gs, "pc_bram", "hmm uncertain stuff")
    labels_b = [i18n.text(x) for x in intent_b.candidates]

    assert "調查這件事 (調查)" in labels_b
    assert "仔細觀察 (察覺)" in labels_b
    assert all("investigate it" not in label for label in labels_b)
    assert all("Perception" not in label for label in labels_b)

    intent_c, _ = await orchestrator.interpret(gs, "pc_bram", "hmm")
    question = i18n.text(intent_c.question)
    labels_c = [i18n.text(x) for x in intent_c.options]

    assert question == "你想做什麼？"
    assert labels_c == ["四處觀察", "找人交談", "仔細檢查某樣東西", "繼續前進"]


def test_intent_prompt_requires_chinese_player_facing_options():
    assert "Traditional Chinese" in prompts.INTENT_SYSTEM
    assert "`question`, `candidates`, and `options`" in prompts.INTENT_SYSTEM


def test_suggested_dc_snaps_to_anchor():
    # Even if a model returns an odd number, it snaps to a 5e anchor.
    p = IntentParse(tier="A", approach="acrobatics", suggested_dc=17)
    assert p.snapped_dc() == 15
    p2 = IntentParse(tier="A", suggested_dc=23)
    assert p2.snapped_dc() == 25


def test_intent_parse_rejects_bad_tier():
    with pytest.raises(Exception):
        IntentParse.model_validate({"tier": "Z"})
