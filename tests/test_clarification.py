"""C-tier follow-up loop — GM clarification stack & prompt convergence.

These prove the fix for the trace.log dead-end: tier-C used to dump 4 hard-coded
options on the player ("威嚇/魅惑/洞察/觀察") and force a button click. Now C tier
posts a natural-language GM follow-up, the player types a free-form /action,
and the parser sees the prior round in CLARIFICATION HISTORY so it can converge
to tier A within 1-2 rounds (capped at MAX_CLARIFICATION_TURNS)."""
from app.ai import prompts
from app.state import game_state


# ───────────────────────── GameState clarification stack ─────────────────────────
def test_push_clarification_records_player_and_question():
    gs = game_state.reset_state(channel_id=1)
    # push captures the triggering utterance AND the GM follow-up in one go.
    n = gs.push_clarification("pc_bram", "前往北方", "兜帽客只是一臉茫然。你接下來怎麼做？")
    assert n == 1
    history = gs.get_clarification("pc_bram")
    assert len(history) == 1
    assert history[0]["player"] == "前往北方"
    assert history[0]["gm"].startswith("兜帽客只是")


def test_push_captures_each_rounds_triggering_utterance():
    # The original goal is never lost: each round stores the player's own words.
    gs = game_state.reset_state(channel_id=1)
    gs.push_clarification("pc_bram", "前往北方", "你想走哪條路線？")
    gs.push_clarification("pc_bram", "跟隨商隊路線", "哪一支商隊？")
    history = gs.get_clarification("pc_bram")
    assert [t["player"] for t in history] == ["前往北方", "跟隨商隊路線"]
    assert history[0]["gm"].startswith("你想走")


def test_clear_clarification_resets_thread():
    gs = game_state.reset_state(channel_id=1)
    gs.push_clarification("pc_bram", "我四處看看", "你想做什麼？")
    gs.clear_clarification("pc_bram")
    assert gs.get_clarification("pc_bram") == []
    assert gs.clarification_turn_count("pc_bram") == 0


def test_two_actors_track_independent_threads():
    gs = game_state.reset_state(channel_id=1)
    gs.push_clarification("pc_bram", "我做點事", "Q1 for Bram")
    gs.push_clarification("pc_lyra", "我看看", "Q1 for Lyra")
    assert gs.clarification_turn_count("pc_bram") == 1
    assert gs.clarification_turn_count("pc_lyra") == 1
    gs.clear_clarification("pc_bram")
    assert gs.clarification_turn_count("pc_bram") == 0
    # Lyra's thread is untouched.
    assert gs.clarification_turn_count("pc_lyra") == 1


def test_clarification_survives_snapshot_roundtrip():
    gs = game_state.reset_state(channel_id=1)
    gs.push_clarification("pc_bram", "我四處看看", "你想做什麼？")

    restored = game_state.GameState.from_dict(gs.to_dict())
    history = restored.get_clarification("pc_bram")
    assert len(history) == 1
    assert history[0]["player"] == "我四處看看"
    assert history[0]["gm"] == "你想做什麼？"


def test_max_clarification_turns_is_three():
    # Bound the loop so the dispatcher knows when to give up.
    assert game_state.GameState.MAX_CLARIFICATION_TURNS == 3


# ───────────────────────── prompt assembly ─────────────────────────
def test_intent_prompt_omits_clarification_block_when_empty():
    gs = game_state.reset_state(channel_id=1)
    ctx = prompts.intent_context(gs, gs.characters["pc_bram"], "詢問商人")
    assert "CLARIFICATION HISTORY" not in ctx


def test_intent_prompt_carries_clarification_history():
    gs = game_state.reset_state(channel_id=1)
    history = [
        {"player": "我抓他衣領", "gm": "兜帽客只是茫然地看著你。你想換個方式問嗎？"},
    ]
    ctx = prompts.intent_context(
        gs, gs.characters["pc_bram"], "再逼他一次",
        clarification=history,
    )
    assert "CLARIFICATION HISTORY" in ctx
    assert "round 1 GM" in ctx
    assert "round 1 player" in ctx
    assert "茫然" in ctx
    assert "抓他衣領" in ctx
    # The player's CURRENT message still appears at the bottom (latest signal).
    assert "再逼他一次" in ctx
    # The convergence directive must be present — otherwise the parser may
    # re-issue the same question instead of narrowing to tier A.
    assert "converge" in ctx.lower() or "tier A" in ctx


def test_intent_prompt_clarification_history_truncates_long_text():
    gs = game_state.reset_state(channel_id=1)
    long_q = "你想做什麼？" + "啊" * 400
    ctx = prompts.intent_context(
        gs, gs.characters["pc_bram"], "繼續",
        clarification=[{"gm": long_q, "player": "繼續"}],
    )
    # Truncation cap inside _clarification_block is 160 chars per turn slot —
    # the raw long string must not bloat the prompt.
    assert ctx.count("啊") < 200


# ───────────────────────── system prompt change ─────────────────────────
def test_intent_system_prompt_describes_gm_follow_up_style():
    """The C-tier instructions must no longer be a forced 'A/B/C/D options' menu."""
    sys = prompts.INTENT_SYSTEM
    # The new directive emphasises GM voice / natural-language question.
    assert "GM follow-up" in sys or "GM 's voice" in sys or "GM" in sys
    assert "natural-language" in sys.lower() or "narrative voice" in sys.lower()
    # Options must be marked OPTIONAL, not the only output channel.
    assert "OPTIONAL" in sys or "optional" in sys
