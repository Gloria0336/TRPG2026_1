"""Dashboard API smoke tests via FastAPI TestClient (no running server needed)."""
import pytest
from fastapi.testclient import TestClient

from app.state import game_state
from app.web.app import app


@pytest.fixture
def client():
    game_state.set_state(None)
    with TestClient(app) as c:
        yield c
    game_state.set_state(None)


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "AI Living World" in r.text


def test_state_empty(client):
    r = client.get("/api/state")
    assert r.status_code == 200
    assert r.json()["started"] is False


def test_state_after_start(client):
    game_state.reset_state(channel_id=42)
    snap = client.get("/api/state").json()
    assert snap["started"] is True
    assert snap["scene"]["id"] == "tavern"
    names = [c["name"] for c in snap["characters"]]
    assert "Bram Ironwood" in names and "Lyra Dawnbringer" in names
    # dashboard_view exposes the structured log the front-end renders
    assert isinstance(snap["log"], list)
