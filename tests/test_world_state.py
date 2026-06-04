"""Step 2 — world state that survives time and revisits.

2a: entity/location notes are capped so a long session can't bloat prompts.
2b: time_of_day is derived from an absolute minute clock and is surfaced to the narrator
    so a stale "清晨" summary can't override established time.
2c: a lasting change to a PLACE persists on the location and shows on revisits.
"""
from app.ai import prompts
from app.db import store
from app.state import game_state
from app.state.game_state import TIME_OF_DAY_STAGES


# ───────────────────────── 2a: notes cap ─────────────────────────
def test_entity_notes_are_capped_and_keep_identity():
    store.seed_entities("tavern", [
        {"id": "ent_perrin", "kind": "person", "name": "老佩林", "notes": "焦急的商人"},
    ])
    for i in range(10):
        store.apply_delta("tavern", {"entity_ref": "老佩林", "note": f"事件{i}"})
    notes = store.find_by_ref("tavern", "老佩林")["notes"]
    lines = [ln for ln in notes.split("\n") if ln.strip()]
    assert len(lines) <= store._MAX_NOTE_LINES
    assert lines[0] == "焦急的商人"      # identity line pinned
    assert "事件9" in lines             # most-recent fact kept
    assert "事件0" not in lines         # oldest fact dropped


# ───────────────────────── 2b: time of day ─────────────────────────
def test_new_game_opens_mid_morning():
    gs = game_state.reset_state(channel_id=0)
    assert gs.flags["world_minutes"] == 540
    assert gs.time_of_day() == "上午"


def test_advance_time_progresses_by_stage_and_wraps_days():
    gs = game_state.reset_state(channel_id=0)
    seen = [gs.advance_time() for _ in range(4)]
    assert seen == ["下午", "傍晚", "夜晚", "清晨"]
    assert gs.time_of_day() == TIME_OF_DAY_STAGES[0] == "清晨"
    assert gs.flags["world_minutes"] == 1740


def test_advance_minutes_can_cross_day_boundaries():
    gs = game_state.reset_state(channel_id=0)
    gs.advance_minutes(16 * 60)
    assert gs.flags["world_minutes"] == 1500
    assert gs.time_of_day() == "夜晚"
    gs.advance_minutes(4 * 60)
    assert gs.flags["world_minutes"] == 1740
    assert gs.time_of_day() == "清晨"


def test_narrate_context_surfaces_time_of_day():
    from app.engine.types import ResolutionResult, ResultKind
    gs = game_state.reset_state(channel_id=0)
    gs.advance_time(3)  # → 夜晚
    result = ResolutionResult(actor_id="pc_bram", actor_name="Bram Ironwood",
                              kind=ResultKind.NARRATIVE, summary="looks around")
    ctx = prompts.narrate_context(gs, result)
    assert "現在時段：夜晚" in ctx


# ───────────────────────── 2c: persistent location state ─────────────────────────
def test_location_state_note_persists_and_caps():
    gs = game_state.reset_state(channel_id=0)  # seeds tavern/east_road/warren locations
    assert store.append_location_state_note("east_road", "絆線已被拆除") is True
    assert "絆線已被拆除" in store.location_state_note("east_road")
    # Non-location id is rejected.
    assert store.append_location_state_note("ent_perrin", "x") is False


def test_location_state_shows_in_summary():
    gs = game_state.reset_state(channel_id=0)
    store.append_location_state_note(gs.current_location_id, "吧台被一拳砸裂")
    summary = prompts.compose_scene_summary(gs)
    assert "持續存在的變化" in summary
    assert "吧台被一拳砸裂" in summary


# ───────────────────────── 2e: NPC agenda ─────────────────────────
def test_npc_agenda_surfaces_as_gm_steering():
    """2e: an NPC's authored agenda reaches the narrator (to drive behaviour) but is
    flagged as hidden steering, not something to reveal outright."""
    from app.engine.types import ResolutionResult, ResultKind
    gs = game_state.reset_state(channel_id=0)  # tavern; 兜帽客 has an authored agenda
    result = ResolutionResult(actor_id="pc_bram", actor_name="Bram Ironwood",
                              kind=ResultKind.NARRATIVE, summary="looks around")
    ctx = prompts.narrate_context(gs, result)
    assert "暗中目標" in ctx        # surfaced as steering
    assert "監視佩林" in ctx        # the authored agenda content


# ───────────────────────── /scene live recap ─────────────────────────
def test_scene_recap_reads_live_state_not_static(monkeypatch):
    """/scene must reflect the CURRENT situation: with AI offline it returns the live
    composed summary, where a departed NPC is marked 已不在場 — unlike the static authored
    blurb that still describes the hooded figure as present."""
    import asyncio
    from app.ai import orchestrator

    monkeypatch.setattr(orchestrator.settings, "ai_offline", True)
    gs = game_state.reset_state(channel_id=0)
    store.apply_delta(gs.current_location_id, {"entity_ref": "兜帽客", "status": "departed"})

    prose = asyncio.run(orchestrator.recap_scene(gs))
    assert "已不在場" in prose      # live state, not the static summary
    assert "老佩林" in prose        # the NPC still present is described


def test_scene_status_embed_lists_present_not_departed():
    from app.discord_bot import embeds

    gs = game_state.reset_state(channel_id=0)
    store.apply_delta(gs.current_location_id, {"entity_ref": "兜帽客", "status": "departed"})
    e = embeds.scene_status_embed(gs, "（測試敘述）")
    fields = " ".join(f.value for f in e.fields)
    assert "老佩林" in fields
    assert "兜帽客" not in fields


def test_scene_status_embed_includes_onboarding_tips_when_given():
    from app.discord_bot import embeds

    gs = game_state.reset_state(channel_id=0)
    e = embeds.scene_status_embed(gs, "x", tips=["試試 A", "試試 B"])
    assert "你可以嘗試" in [f.name for f in e.fields]


def test_open_scene_offline_is_dynamic_live_not_static(monkeypatch):
    """The /start opening (open_scene) is dynamic too: with AI offline it returns the LIVE
    composed summary (a departed NPC is marked 已不在場), never the static authored blurb."""
    import asyncio
    from app.ai import orchestrator

    monkeypatch.setattr(orchestrator.settings, "ai_offline", True)
    gs = game_state.reset_state(channel_id=0)
    store.apply_delta(gs.current_location_id, {"entity_ref": "兜帽客", "status": "departed"})
    prose = asyncio.run(orchestrator.open_scene(gs))
    assert "已不在場" in prose
