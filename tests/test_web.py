"""Dashboard API smoke tests via FastAPI TestClient (no running server needed)."""
import pytest
from fastapi.testclient import TestClient

from app.state import game_state
from app.web.app import app
from app.web.portal_app import app as portal_app


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
    assert 'href="./static/style.css"' in r.text
    assert 'src="./static/app.js"' in r.text
    assert 'href="/static/style.css"' not in r.text
    assert 'src="/static/app.js"' not in r.text


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


def test_portal_me_public_state(client):
    game_state.reset_state(channel_id=42)
    r = client.get("/api/portal/me")
    assert r.status_code == 200
    snap = r.json()
    assert snap["viewer"] is None
    assert snap["campaign"]["started"] is True
    assert {c["id"] for c in snap["characters"]} >= {"pc_bram", "pc_lyra"}


def test_portal_discord_login_requires_oauth_config(client, monkeypatch):
    from app.web import portal_api

    monkeypatch.setattr(portal_api, "_oauth_ready", lambda: False)
    r = client.get("/api/portal/auth/discord/login")
    assert r.status_code == 503
    assert r.json()["error"] == "Discord OAuth is not configured"


def test_portal_claim_character(client, monkeypatch):
    from app.web import portal_api

    game_state.reset_state(channel_id=42)
    monkeypatch.setattr(
        portal_api,
        "_session_user",
        lambda request: {"id": "user-1", "username": "lo", "global_name": "LO"},
    )
    r = client.post("/api/portal/characters/pc_bram/claim")
    assert r.status_code == 200
    snap = r.json()
    assert snap["viewer"]["global_name"] == "LO"
    assert snap["player_status"]["claimed_pc_id"] == "pc_bram"


def test_portal_create_character(client, monkeypatch):
    from app.web import portal_api

    game_state.reset_state(channel_id=42)
    monkeypatch.setattr(
        portal_api,
        "_session_user",
        lambda request: {"id": "user-2", "username": "player", "global_name": "Player"},
    )
    r = client.post(
        "/api/portal/characters",
        json={"name": "Sable Vey", "archetype": "scout", "portrait": "弓", "blurb": "安靜的斥候。"},
    )
    assert r.status_code == 201
    snap = r.json()
    created = snap["player_status"]["character"]
    assert created["name"] == "Sable Vey"
    assert created["id"] in {c["id"] for c in snap["characters"]}


def test_public_portal_serves_player_entry_only():
    game_state.set_state(None)
    with TestClient(portal_app) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert "TRPG 玩家入口" in r.text
        assert 'src="./app.js"' in r.text

        game_state.reset_state(channel_id=42)
        portal_state = c.get("/api/portal/me")
        assert portal_state.status_code == 200
        assert portal_state.json()["campaign"]["started"] is True

        assert c.get("/api/state").status_code == 404
        assert c.get("/api/ai/health").status_code == 404
        assert c.get("/api/stream").status_code == 404
    game_state.set_state(None)
