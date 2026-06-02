"""Step 4 — goal director (soft story spine) replaces the rigid scene index.

Beats complete on structured world signals (flags + the visited-locations trail), not a
fixed scene order, so players can reach them out of order or skip them and the world
follows. The director only tracks and nudges; it never drags the party back.
"""
from app.content import director, scenario
from app.state import game_state


def test_fresh_game_active_beat_is_accept_quest():
    gs = game_state.reset_state(channel_id=0)
    ev = director.evaluate(gs)
    assert ev["done"] == []
    assert ev["active"]["id"] == "accept_quest"
    assert ev["all_done"] is False


def test_reaching_east_road_completes_first_beat():
    gs = game_state.reset_state(channel_id=0)
    gs.goto_scene(scenario.scene_by_id("east_road"))
    ev = director.evaluate(gs)
    assert "accept_quest" in ev["done"]
    assert ev["active"]["id"] == "find_caravan"
    assert ev["all_done"] is False


def test_reaching_warren_completes_through_find_caravan():
    gs = game_state.reset_state(channel_id=0)
    gs.goto_scene(scenario.scene_by_id("warren"))
    ev = director.evaluate(gs)
    # warren satisfies done_if_reached for both earlier beats.
    assert {"accept_quest", "find_caravan"} <= set(ev["done"])
    assert ev["active"]["id"] == "confront_leader"


def test_skipped_beats_detected_when_climax_reached_early():
    gs = game_state.reset_state(channel_id=0)  # still at tavern, nothing visited beyond it
    gs.flags["climax_resolved"] = True         # e.g. boss dealt with via an unexpected route
    ev = director.evaluate(gs)
    assert ev["all_done"] is True
    assert set(ev["skipped"]) == {"accept_quest", "find_caravan"}


def test_record_reports_newly_done_once_and_persists():
    gs = game_state.reset_state(channel_id=0)
    gs.goto_scene(scenario.scene_by_id("east_road"))
    first = director.record(gs)
    assert "accept_quest" in first["newly_done"]
    assert gs.flags["goals_done"] == first_done(gs)
    second = director.record(gs)
    assert second["newly_done"] == []  # idempotent: no double-announce


def first_done(gs):
    return director.evaluate(gs)["done"]


def test_stall_nudge_fires_at_threshold_then_resets():
    gs = game_state.reset_state(channel_id=0)
    assert director.nudge_if_stalled(gs) is None         # 0 beats, no stall
    for _ in range(director.STALL_THRESHOLD):
        director.note_beat(gs)
    hint = director.nudge_if_stalled(gs)
    assert hint and "老佩林" in hint                      # active beat's in-world nudge
    assert director.nudge_if_stalled(gs) is None         # counter reset after firing


def test_terminal_flag_marks_all_done():
    gs = game_state.reset_state(channel_id=0)
    gs.goto_scene(scenario.scene_by_id("warren"))
    assert director.evaluate(gs)["all_done"] is False
    gs.flags["climax_resolved"] = True
    assert director.evaluate(gs)["all_done"] is True
