"""SQLite memory store tests — event history + scene_state round-trips."""
import time

from app.db import store


def test_init_is_idempotent():
    store.init_db()
    store.init_db()  # second call must not error or wipe anything


def test_event_roundtrip_and_narration_update():
    store.insert_event(
        id="ev1", scene_id="tavern", actor_id="pc_bram", actor_name="Bram",
        kind="check", summary="Persuasion check vs DC 13: FAILURE", narration="",
        scope="local", data={"target_name": "老佩林"}, ts=time.time(),
    )
    events = store.recent_events("tavern", 10)
    assert len(events) == 1
    assert events[0]["summary"].endswith("FAILURE")
    assert events[0]["data"]["target_name"] == "老佩林"

    store.update_narration("ev1", "他壓低聲音逼問，但對方退縮了。")
    events = store.recent_events("tavern", 10)
    assert events[0]["narration"].startswith("他壓低聲音")


def test_recent_events_orders_oldest_to_newest_and_limits():
    base = time.time()
    for i in range(5):
        store.insert_event(
            id=f"e{i}", scene_id="east_road", actor_id="pc", actor_name="P",
            kind="check", summary=f"s{i}", narration="", scope="local",
            data={}, ts=base + i,
        )
    last3 = store.recent_events("east_road", 3)
    assert [e["summary"] for e in last3] == ["s2", "s3", "s4"]


def test_scene_state_base_and_current():
    store.set_base_summary("tavern", "靜態背景")
    store.set_current_summary("tavern", "動態現況")
    ss = store.get_scene_state("tavern")
    assert ss["base_summary"] == "靜態背景"
    assert ss["current_summary"] == "動態現況"


def test_reset_world_clears_everything():
    store.insert_event(
        id="z", scene_id="tavern", actor_id="x", actor_name="X", kind="k",
        summary="s", narration="", scope="local", data={}, ts=time.time(),
    )
    store.set_base_summary("tavern", "x")
    store.reset_world()
    assert store.recent_events("tavern", 10) == []
    assert store.get_scene_state("tavern") is None
    assert store.get_all("tavern") == []
