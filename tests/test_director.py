"""A-sandbox contract: the goal spine was removed (scenario.GOALS == []).

The director still loads and is still called by the bot, but it is now INERT — there is no
active beat, nothing ever completes, and the world never auto-ends. These tests pin that
contract so a future re-introduction of goals is a deliberate, visible change.
"""
from app.content import director, scenario
from app.state import game_state


def test_goals_are_empty_under_sandbox():
    assert scenario.GOALS == []


def test_director_is_inert_with_no_goals():
    gs = game_state.reset_state(channel_id=0)
    ev = director.evaluate(gs)
    assert ev["done"] == []
    assert ev["skipped"] == []
    assert ev["active"] is None
    assert ev["all_done"] is False


def test_record_reports_nothing_and_never_ends():
    gs = game_state.reset_state(channel_id=0)
    progress = director.record(gs)
    assert progress["newly_done"] == []
    assert progress["all_done"] is False
    # Even after reaching the old climax location, nothing auto-completes the story.
    gs.goto_scene(scenario.scene_by_id("warren"))
    assert director.record(gs)["all_done"] is False


def test_stall_nudge_is_silent_without_an_active_beat():
    gs = game_state.reset_state(channel_id=0)
    for _ in range(director.STALL_THRESHOLD + 1):
        director.note_beat(gs)
    assert director.nudge_if_stalled(gs) is None
