"""Shared test fixtures. Reseeds the engine RNG before every test for determinism,
and isolates the SQLite memory store to a per-test temp DB so tests never touch the
real save/world.db."""
import pytest

from app.config import settings
from app.db import store
from app.engine import dice


@pytest.fixture(autouse=True)
def _seed_dice():
    dice.reseed(12345)
    yield


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path):
    settings.db_path = tmp_path / "test_world.db"
    store.close()
    store.init_db()
    yield
    store.close()
