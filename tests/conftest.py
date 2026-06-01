"""Shared test fixtures. Reseeds the engine RNG before every test for determinism."""
import pytest

from app.engine import dice


@pytest.fixture(autouse=True)
def _seed_dice():
    dice.reseed(12345)
    yield
