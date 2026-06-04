"""Per-campaign DB instancing (案 A): each campaign gets its own world.db/session.json,
runtime data stays isolated from the starter content, and retention keeps only the
newest finished campaigns. conftest._isolated_db points settings.campaigns_dir at a
per-test tmp dir, so these exercise the real filesystem behaviour in isolation."""
from __future__ import annotations

import json

from app.config import settings
from app.db import store
from app.state import campaigns


def _open(ref: campaigns.CampaignRef) -> None:
    """Repoint the global store at a specific campaign db (mirrors _point_store_at)."""
    settings.db_path = ref.world_db_path
    store.close()
    store.init_db(ref.world_db_path)


def _write_finished(cid: str, *, created: float, ended: float) -> None:
    d = settings.campaigns_dir / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / "world.db").write_text("", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({
        "id": cid, "status": "finished", "created_ts": created, "ended_ts": ended,
    }), encoding="utf-8")


def test_begin_new_creates_dir_with_empty_schema():
    ref = campaigns.begin_new(channel_id=1, title="T")
    assert ref.dir.exists()
    assert ref.world_db_path.exists()
    meta = json.loads(ref.meta_path.read_text(encoding="utf-8"))
    assert meta["status"] == "active"
    assert meta["channel_id"] == 1
    # Schema exists but the world is empty (no starter pollution carried over).
    assert store.get_all("anywhere") == []
    assert campaigns.active().id == ref.id


def test_two_campaigns_are_isolated():
    a = campaigns.begin_new(channel_id=1)
    store.upsert_entity(id="ent_x", scene_id="s", kind="person", name="X")
    # Opening a second campaign must not see the first one's runtime data.
    campaigns.begin_new(channel_id=2)
    assert store.get_entity_by_id("ent_x") is None
    # ...and the first campaign still has it.
    _open(a)
    assert store.get_entity_by_id("ent_x")["name"] == "X"


def test_retention_keeps_newest_finished_and_spares_active():
    active_ref = campaigns.begin_new(channel_id=99)  # active, must never be evicted
    for i in range(11):  # 11 finished, cap 10 -> oldest one evicted
        _write_finished(f"cmp_fin_{i:02d}", created=1000 + i, ended=2000 + i)
    deleted = campaigns.enforce_retention(max_finished=10)
    assert deleted == ["cmp_fin_00"]
    remaining = {m["id"] for m in campaigns.list_campaigns()}
    assert "cmp_fin_00" not in remaining
    assert "cmp_fin_10" in remaining
    assert active_ref.id in remaining  # active survives
    assert active_ref.dir.exists()


def test_in_progress_campaigns_do_not_count():
    # 12 active (in-progress) dirs should never be evicted by retention.
    for i in range(12):
        d = settings.campaigns_dir / f"cmp_act_{i:02d}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "meta.json").write_text(json.dumps({
            "id": f"cmp_act_{i:02d}", "status": "active", "created_ts": 1000 + i,
        }), encoding="utf-8")
    assert campaigns.enforce_retention(max_finished=10) == []
    assert len(campaigns.list_campaigns()) == 12


def test_resume_latest_picks_newest_created(tmp_path):
    a = campaigns.begin_new(channel_id=1)
    b = campaigns.begin_new(channel_id=2)
    # Force distinct created_ts so "latest" is unambiguous regardless of clock resolution.
    for ref, ts in ((a, 1000.0), (b, 2000.0)):
        meta = json.loads(ref.meta_path.read_text(encoding="utf-8"))
        meta["created_ts"] = ts
        ref.meta_path.write_text(json.dumps(meta), encoding="utf-8")
    campaigns.reset()
    resumed = campaigns.resume_latest()
    assert resumed.id == b.id
    assert settings.db_path == b.world_db_path


def test_resume_latest_none_when_empty():
    assert campaigns.resume_latest() is None


def test_migrate_legacy_folds_single_save(tmp_path):
    legacy_db = tmp_path / "legacy_world.db"
    legacy_session = tmp_path / "legacy_session.json"
    # Build a real legacy db with one entity via the store.
    settings.db_path = legacy_db
    store.close()
    store.init_db(legacy_db)
    store.upsert_entity(id="ent_old", scene_id="s", kind="person", name="Old")
    store.close()
    legacy_session.write_text(
        json.dumps({"flags": {"over": True}, "channel_id": 7,
                    "scene": {"title": "尾聲"}}),
        encoding="utf-8",
    )

    ref = campaigns.migrate_legacy_if_needed(legacy_db=legacy_db, legacy_session=legacy_session)
    assert ref is not None
    assert ref.world_db_path.exists() and ref.session_path.exists()
    assert not legacy_db.exists()  # moved, not copied
    meta = json.loads(ref.meta_path.read_text(encoding="utf-8"))
    assert meta["status"] == "finished"  # inferred from flags.over
    assert meta["channel_id"] == 7
    # Data carried over intact.
    _open(ref)
    assert store.get_entity_by_id("ent_old")["name"] == "Old"

    # No-op once a campaign dir exists.
    assert campaigns.migrate_legacy_if_needed(
        legacy_db=legacy_db, legacy_session=legacy_session) is None


def test_mark_finished_is_idempotent():
    ref = campaigns.begin_new(channel_id=1)
    campaigns.mark_finished(outcome="win")
    meta1 = json.loads(ref.meta_path.read_text(encoding="utf-8"))
    assert meta1["status"] == "finished"
    ended_first = meta1["ended_ts"]
    campaigns.mark_finished(outcome="ignored")  # second call must not overwrite
    meta2 = json.loads(ref.meta_path.read_text(encoding="utf-8"))
    assert meta2["ended_ts"] == ended_first
    assert meta2["outcome"] == "win"
