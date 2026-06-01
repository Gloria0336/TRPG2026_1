"""Tests for §8.0 narration guard.

The guard is a pure function — `find_violations(prose, result) -> list[str]`. These
tests pin its behaviour: clean fiction passes, mechanical leakage is caught, and the
common false-positive shapes (incidental Chinese numerals, engine-published numbers
echoed back) do not trip it.
"""
import pytest

from app.ai import guard, orchestrator, prompts
from app.config import settings
from app.engine.types import ResolutionResult, ResultBand, ResultKind
from app.state import game_state


# ───────────────────────── fixtures ─────────────────────────
def _check_result(damage: int | None = None, healing: int | None = None,
                  deltas: list[str] | None = None,
                  kind: ResultKind = ResultKind.CHECK) -> ResolutionResult:
    return ResolutionResult(
        kind=kind,
        actor_id="pc_bram",
        actor_name="Bram",
        summary="Test result",
        success=True,
        band=ResultBand.SUCCESS,
        damage=damage,
        healing=healing,
        deltas=deltas or [],
    )


# ───────────────────────── clean prose passes ─────────────────────────
def test_clean_fiction_passes():
    res = _check_result()
    prose = "布拉姆穩穩握住長劍，劍尖在晨光中劃出一道弧線。"
    assert guard.find_violations(prose, res) == []


def test_chinese_numerals_do_not_false_positive():
    """Incidental fiction numerals — 兩三隻、走了幾百步 — must pass."""
    res = _check_result()
    prose = "三隻哥布林從樹後撲出來，他走了幾百步才看見牠們，兩條繩索懸在岩石之間。"
    assert guard.find_violations(prose, res) == []


def test_echoing_engine_published_numbers_is_fine():
    """If a delta literally says '6 damage', the narrator may echo '6 點傷害'."""
    res = _check_result(damage=6, deltas=["Goblin takes 6 damage (5→-1 HP)"])
    prose = "長劍劈中哥布林，造成 6 點傷害。"
    assert guard.find_violations(prose, res) == []


# ───────────────────────── mechanic leakage caught ─────────────────────────
def test_dc_reveal_is_rejected():
    res = _check_result()
    assert any("DC" in v or "難度等級" in v
               for v in guard.find_violations("這需要 DC 15 的察覺檢定。", res))
    assert any("DC" in v or "難度等級" in v
               for v in guard.find_violations("難度等級 20 的攀爬。", res))


def test_dice_notation_is_rejected():
    res = _check_result()
    assert any("dice notation" in v for v in guard.find_violations("他擲出 d20。", res))
    assert any("dice notation" in v for v in guard.find_violations("造成 2d8 的傷害。", res))


def test_modifier_phrases_are_rejected():
    res = _check_result()
    assert any("modifier" in v for v in guard.find_violations("他得到 +3 加值。", res))
    assert any("modifier" in v for v in guard.find_violations("獲得 +5 bonus.", res))


def test_dice_talk_is_rejected():
    res = _check_result()
    assert any("擲骰" in v or "dice" in v.lower()
               for v in guard.find_violations("玩家擲骰決定。", res))


def test_save_mention_on_non_save_result_is_rejected():
    res = _check_result(kind=ResultKind.CHECK)
    violations = guard.find_violations("他必須進行豁免，才能避過。", res)
    assert any("豁免" in v for v in violations)


def test_save_mention_on_save_result_is_fine():
    res = _check_result(kind=ResultKind.SAVE)
    violations = guard.find_violations("他必須進行豁免，才能避過。", res)
    # No 豁免-on-non-save violation; may still trip other rules but not this one.
    assert not any("非-save" in v or "non-save" in v for v in violations)


# ───────────────────────── damage / heal number mismatch ─────────────────────────
def test_damage_number_mismatch_is_rejected():
    """Result says 0 damage; prose claims '8 點傷害' — fabricated mechanical outcome."""
    res = _check_result(damage=None)
    violations = guard.find_violations("造成 8 點傷害。", res)
    assert any("damage number mismatch" in v for v in violations)


def test_damage_number_matching_result_is_fine():
    res = _check_result(damage=7)
    assert guard.find_violations("劍鋒劃過，造成 7 點傷害。", res) == []


def test_healing_number_mismatch_is_rejected():
    res = _check_result(healing=None, kind=ResultKind.HEAL)
    violations = guard.find_violations("恢復 10 點生命值。", res)
    assert any("healing number mismatch" in v for v in violations)


def test_healing_number_matching_result_is_fine():
    res = _check_result(healing=5, kind=ResultKind.HEAL)
    assert guard.find_violations("溫暖的光輝恢復 5 點生命值。", res) == []


# ───────────────────────── orchestrator wiring ─────────────────────────
class _StubChat:
    """Replaces orchestrator._chat for tests; queues canned responses."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[tuple[str, str, str]] = []   # (model, system, user)

    async def __call__(self, model, system, user, *, json_mode=False, max_tokens=400):
        self.calls.append((model, system, user))
        if not self.replies:
            raise RuntimeError("stub chat ran out of replies")
        return self.replies.pop(0)


@pytest.fixture
def _online(monkeypatch):
    """Pretend the AI is online (offline mode bypasses the whole narrate pipeline)."""
    monkeypatch.setattr(settings, "ai_offline", False)
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    yield


def _fresh():
    return game_state.new_game(channel_id=1)


async def test_orchestrator_returns_clean_prose_directly(_online, monkeypatch):
    gs = _fresh()
    res = _check_result()
    stub = _StubChat(["布拉姆穩穩劈出長劍，俐落結束這一回合。"])
    monkeypatch.setattr(orchestrator, "_chat", stub)

    prose = await orchestrator.narrate(gs, res)
    assert "布拉姆" in prose
    assert len(stub.calls) == 1   # no retry needed


async def test_orchestrator_retries_once_on_violation(_online, monkeypatch):
    gs = _fresh()
    res = _check_result()
    stub = _StubChat([
        "他成功了，DC 15 的察覺檢定通過。",   # mechanic leak → retry
        "他眼神一銳，瞄到了暗影中的兜帽客。",  # clean
    ])
    monkeypatch.setattr(orchestrator, "_chat", stub)

    prose = await orchestrator.narrate(gs, res)
    assert prose == "他眼神一銳，瞄到了暗影中的兜帽客。"
    assert len(stub.calls) == 2
    # The retry user prompt must include the guard reminder so the model can self-correct.
    assert "GUARD" in stub.calls[1][2]


async def test_orchestrator_falls_back_to_canned_when_retry_also_fails(_online, monkeypatch):
    gs = _fresh()
    res = _check_result(deltas=["Bram succeeded"])
    stub = _StubChat([
        "DC 15 的察覺檢定。",   # violation
        "他擲出 d20 又中了。",   # still violating
    ])
    monkeypatch.setattr(orchestrator, "_chat", stub)

    prose = await orchestrator.narrate(gs, res)
    # Canned fallback uses the engine's own hint/deltas; neither should leak mechanics.
    assert "DC" not in prose
    assert "d20" not in prose
    assert len(stub.calls) == 2


# ───────────────────────── reminder text ─────────────────────────
def test_violation_reminder_includes_each_violation():
    reminder = guard.violation_reminder(["dice notation", "explicit DC"])
    assert "dice notation" in reminder
    assert "explicit DC" in reminder
    assert "GUARD" in reminder


def test_violation_reminder_empty_for_no_violations():
    assert guard.violation_reminder([]) == ""


# ───────────────────────── existing prompt still describes guard rules ─────────────────────────
def test_narrator_system_prompt_mentions_no_numbers_rule():
    """The prompt-level guard (text-only) plus the code-level guard form layered defence."""
    assert "ABSOLUTE RULES" in prompts.NARRATE_SYSTEM
    assert "Never contradict or change any number" in prompts.NARRATE_SYSTEM
