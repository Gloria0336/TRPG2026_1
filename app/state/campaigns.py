"""Per-campaign storage (案 A): authored starter content stays in code, but every
campaign gets its OWN DB instance so runtime-generated data never pollutes the starter.

A campaign lives in its own directory under ``settings.campaigns_dir``:

    <campaigns_dir>/<id>/world.db      # this campaign's SQLite continuity store
    <campaigns_dir>/<id>/session.json  # the GameState snapshot (HP/flags/combat/...)
    <campaigns_dir>/<id>/meta.json     # {id, channel_id, title, status, created_ts, ended_ts, outcome}

This module is the ONLY place that repoints ``settings.db_path``. The low-level
``store`` stays oblivious (it just opens ``settings.db_path``), keeping the dependency
one-way — and letting tests point ``settings.db_path`` at a temp file directly.

Retention: only FINISHED campaigns count toward the cap; in-progress dirs accumulate
and are never auto-evicted. Eviction runs on /finish (mark_finished), newest kept.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..config import SAVE_DIR, settings
from ..db import store
from ..logging_setup import get_logger

log = get_logger("campaigns")

_META_NAME = "meta.json"
_WORLD_DB_NAME = "world.db"
_SESSION_NAME = "session.json"

STATUS_ACTIVE = "active"
STATUS_FINISHED = "finished"


@dataclass
class CampaignRef:
    id: str
    dir: Path

    @property
    def world_db_path(self) -> Path:
        return self.dir / _WORLD_DB_NAME

    @property
    def session_path(self) -> Path:
        return self.dir / _SESSION_NAME

    @property
    def meta_path(self) -> Path:
        return self.dir / _META_NAME


# Module-level pointer to the campaign whose DB is currently open. Single-session MVP.
_active: CampaignRef | None = None


# ───────────────────────── helpers ─────────────────────────
def _root() -> Path:
    return Path(settings.campaigns_dir)


def _read_meta(d: Path) -> dict | None:
    try:
        return json.loads((d / _META_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_meta(ref: CampaignRef, meta: dict) -> None:
    ref.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _campaign_dirs() -> list[Path]:
    root = _root()
    if not root.exists():
        return []
    return [d for d in root.iterdir() if d.is_dir() and (d / _META_NAME).exists()]


def _new_id() -> str:
    return f"cmp_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _point_store_at(ref: CampaignRef) -> None:
    """Repoint the single global DB connection at this campaign's world.db, creating
    the schema if the file is new. Closing first releases the previous handle (so a
    later retention rmtree on Windows can't hit a locked file)."""
    settings.db_path = ref.world_db_path
    store.close()
    store.init_db(ref.world_db_path)


# ───────────────────────── lifecycle ─────────────────────────
def begin_new(channel_id: int | None = None, *, title: str = "") -> CampaignRef:
    """Open a brand-new, empty campaign DB and make it active. Seeding of starter
    content (locations/entities) is the caller's job (see game_state.new_game)."""
    global _active
    cid = _new_id()
    d = _root() / cid
    d.mkdir(parents=True, exist_ok=True)
    ref = CampaignRef(id=cid, dir=d)
    _point_store_at(ref)
    _write_meta(ref, {
        "id": cid,
        "channel_id": channel_id,
        "title": title,
        "status": STATUS_ACTIVE,
        "created_ts": time.time(),
        "ended_ts": None,
        "outcome": None,
    })
    _active = ref
    log.info("campaign: begin_new %s (channel=%s)", cid, channel_id)
    return ref


def mark_finished(outcome: str = "") -> None:
    """Mark the active campaign finished and enforce retention. Idempotent — calling
    it again on an already-finished campaign only re-runs retention."""
    if _active is None:
        return
    meta = _read_meta(_active.dir) or {}
    if meta.get("status") != STATUS_FINISHED:
        meta["status"] = STATUS_FINISHED
        meta["ended_ts"] = time.time()
        meta["outcome"] = (outcome or meta.get("outcome") or "")[:200]
        _write_meta(_active, meta)
        log.info("campaign: mark_finished %s", _active.id)
    enforce_retention()


def enforce_retention(max_finished: int | None = None) -> list[str]:
    """Keep only the newest ``max_finished`` FINISHED campaigns; delete older ones.
    In-progress campaigns and the active campaign are never evicted. Returns the ids
    that were deleted."""
    cap = settings.max_finished_campaigns if max_finished is None else max_finished
    finished: list[tuple[float, Path, str]] = []
    for d in _campaign_dirs():
        if _active is not None and d == _active.dir:
            continue
        meta = _read_meta(d)
        if not meta or meta.get("status") != STATUS_FINISHED:
            continue
        ts = meta.get("ended_ts") or meta.get("created_ts") or 0.0
        finished.append((float(ts), d, meta.get("id", d.name)))
    finished.sort(key=lambda x: x[0], reverse=True)  # newest first
    deleted: list[str] = []
    for _ts, d, cid in finished[cap:]:
        try:
            shutil.rmtree(d)
            deleted.append(cid)
            log.info("campaign: retention evicted %s", cid)
        except OSError as exc:  # noqa: PERF203 — best-effort cleanup
            log.warning("campaign: retention failed to delete %s: %s", cid, exc)
    return deleted


def resume_latest() -> CampaignRef | None:
    """Open the most-recently-created campaign (by created_ts) and make it active.
    Returns None when no campaign exists yet (fresh install)."""
    global _active
    dirs = _campaign_dirs()
    if not dirs:
        return None

    def created(d: Path) -> float:
        return float((_read_meta(d) or {}).get("created_ts") or 0.0)

    latest = max(dirs, key=created)
    meta = _read_meta(latest) or {}
    ref = CampaignRef(id=meta.get("id", latest.name), dir=latest)
    _point_store_at(ref)
    _active = ref
    log.info("campaign: resumed %s", ref.id)
    return ref


def migrate_legacy_if_needed(
    legacy_db: Path | None = None, legacy_session: Path | None = None
) -> CampaignRef | None:
    """One-time upgrade: fold a pre-refactor single save/world.db (+ session.json) into
    a campaign directory so the in-progress game survives. No-op once any campaign dir
    exists or there is no legacy DB. Touches the filesystem only — call resume_latest()
    afterwards to actually activate it."""
    if _campaign_dirs():
        return None
    legacy_db = legacy_db or (SAVE_DIR / "world.db")
    legacy_session = legacy_session or (SAVE_DIR / "session.json")
    if not legacy_db.exists():
        return None

    status = STATUS_ACTIVE
    channel_id = None
    title = ""
    if legacy_session.exists():
        try:
            snap = json.loads(legacy_session.read_text(encoding="utf-8"))
            if (snap.get("flags") or {}).get("over"):
                status = STATUS_FINISHED
            channel_id = snap.get("channel_id")
            title = (snap.get("scene") or {}).get("title") or ""
        except (OSError, json.JSONDecodeError):
            pass

    created = legacy_db.stat().st_mtime
    cid = _new_id()
    d = _root() / cid
    d.mkdir(parents=True, exist_ok=True)
    ref = CampaignRef(id=cid, dir=d)
    shutil.move(str(legacy_db), str(ref.world_db_path))
    if legacy_session.exists():
        shutil.move(str(legacy_session), str(ref.session_path))
    _write_meta(ref, {
        "id": cid,
        "channel_id": channel_id,
        "title": title,
        "status": status,
        "created_ts": created,
        "ended_ts": created if status == STATUS_FINISHED else None,
        "outcome": None,
    })
    log.info("campaign: migrated legacy save into %s (status=%s)", cid, status)
    return ref


# ───────────────────────── accessors ─────────────────────────
def active() -> CampaignRef | None:
    return _active


def active_world_db_path() -> Path | None:
    return _active.world_db_path if _active else None


def active_session_path() -> Path | None:
    return _active.session_path if _active else None


def list_campaigns() -> list[dict]:
    """All campaign meta dicts, newest first by created_ts."""
    metas = [m for d in _campaign_dirs() if (m := _read_meta(d))]
    metas.sort(key=lambda m: float(m.get("created_ts") or 0.0), reverse=True)
    return metas


def reset() -> None:
    """Forget the active campaign pointer (test isolation)."""
    global _active
    _active = None
