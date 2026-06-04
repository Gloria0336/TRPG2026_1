"""Player portal API.

The localhost dashboard remains read-only. These routes are for the separate
player portal: Discord OAuth, player status, quest board, and
lightweight player-created character records.
"""
from __future__ import annotations

import secrets
import time
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from ..config import settings
from ..content import scenario
from ..engine.types import Action, ActionType, Character, Damage, Event
from ..state import game_state
from ..world import location_registration


router = APIRouter(prefix="/api/portal")

DISCORD_API_BASE = "https://discord.com/api/v10"
DISCORD_AUTH_URL = "https://discord.com/oauth2/authorize"
DISCORD_TOKEN_URL = f"{DISCORD_API_BASE}/oauth2/token"


class CharacterCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=48)
    archetype: str = Field(default="adventurer", max_length=32)
    portrait: str = Field(default="星", max_length=4)
    blurb: str = Field(default="", max_length=160)


def _oauth_ready() -> bool:
    return bool(
        settings.discord_oauth_client_id
        and settings.discord_oauth_client_secret
        and settings.discord_oauth_redirect_uri
    )


def _portal_redirect(path: str = "") -> str:
    base = settings.portal_public_url.rstrip("/") or "http://localhost:8000"
    if not path:
        return base
    return f"{base}/{path.lstrip('/')}"


def _session_user(request: Request) -> dict[str, Any] | None:
    user = request.session.get("discord_user")
    return dict(user) if isinstance(user, dict) else None


def _require_user(request: Request) -> dict[str, Any]:
    user = _session_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Discord login required")
    return user


def _public_character(c: Character, gs: game_state.GameState | None) -> dict:
    claim = gs.claim_for_pc(c.id) if gs and c.is_pc else None
    return {
        "id": c.id,
        "name": c.name,
        "is_pc": c.is_pc,
        "portrait": c.portrait,
        "level": c.level,
        "hp": c.hp,
        "max_hp": c.max_hp,
        "ac": c.ac,
        "conditions": c.conditions,
        "blurb": c.blurb,
        "abilities": c.abilities,
        "actions": [a.name for a in c.actions],
        "claim": claim,
    }


def _player_status(gs: game_state.GameState | None, user_id: str | None) -> dict:
    if not gs or not user_id:
        return {"claimed_pc_id": None, "character": None, "turn": None}
    pc = gs.pc_for_user(user_id)
    turn_id = gs.pending_freeplay_actor_id() or gs.current_freeplay_actor_id()
    return {
        "claimed_pc_id": pc.id if pc else None,
        "character": _public_character(pc, gs) if pc else None,
        "turn": {
            "current_pc_id": turn_id,
            "is_yours": bool(pc and turn_id == pc.id),
        },
    }


def _quest_payload(gs: game_state.GameState | None) -> list[dict]:
    return (gs.dashboard_view().get("quests") or []) if gs else []


def _portal_state(request: Request) -> dict:
    gs = game_state.get_state()
    user = _session_user(request)
    user_id = str(user["id"]) if user and user.get("id") else None
    return {
        "viewer": user,
        "campaign": {
            "started": bool(gs and gs.started),
            "version": gs.version if gs else -1,
            "scene": gs.scene.to_dict() if gs else None,
        },
        "player_status": _player_status(gs, user_id),
        "characters": [_public_character(c, gs) for c in gs.pcs()] if gs else [],
        "quests": _quest_payload(gs),
    }


def _character_from_request(data: CharacterCreateRequest, user_id: str) -> Character:
    stamp = secrets.token_hex(4)
    archetype = data.archetype.strip().lower() or "adventurer"
    profile = {
        "guardian": {
            "abilities": {"STR": 16, "DEX": 11, "CON": 15, "INT": 10, "WIS": 12, "CHA": 10},
            "max_hp": 14,
            "ac": 17,
            "action": Action(
                name="Reliable Strike",
                type=ActionType.MELEE_ATTACK,
                to_hit=5,
                damage=[Damage(1, 8, 3, "slashing")],
                description="A steady close-range weapon attack.",
            ),
            "blurb": "穩健的前排守護者。",
        },
        "mystic": {
            "abilities": {"STR": 8, "DEX": 12, "CON": 13, "INT": 16, "WIS": 14, "CHA": 10},
            "max_hp": 10,
            "ac": 13,
            "action": Action(
                name="Arcane Spark",
                type=ActionType.RANGED_ATTACK,
                to_hit=5,
                damage=[Damage(1, 10, 0, "force")],
                range_ft=60,
                description="A compact ranged spell attack.",
            ),
            "blurb": "以奧術解題的施法者。",
        },
        "scout": {
            "abilities": {"STR": 10, "DEX": 16, "CON": 13, "INT": 12, "WIS": 14, "CHA": 10},
            "max_hp": 11,
            "ac": 15,
            "action": Action(
                name="Quick Shot",
                type=ActionType.RANGED_ATTACK,
                to_hit=5,
                damage=[Damage(1, 6, 3, "piercing")],
                range_ft=80,
                description="A fast ranged attack from cover or distance.",
            ),
            "blurb": "擅長偵查與遠距支援。",
        },
    }.get(archetype)
    if profile is None:
        archetype = "adventurer"
        profile = {
            "abilities": {"STR": 12, "DEX": 12, "CON": 12, "INT": 12, "WIS": 12, "CHA": 12},
            "max_hp": 12,
            "ac": 14,
            "action": Action(
                name="Improvised Attack",
                type=ActionType.MELEE_ATTACK,
                to_hit=4,
                damage=[Damage(1, 6, 2, "bludgeoning")],
                description="A flexible basic attack.",
            ),
            "blurb": "剛踏入冒險的多面手。",
        }
    blurb = data.blurb.strip() or profile["blurb"]
    return Character(
        id=f"pc_custom_{user_id}_{stamp}",
        name=data.name.strip(),
        is_pc=True,
        level=1,
        abilities=profile["abilities"],
        max_hp=profile["max_hp"],
        hp=profile["max_hp"],
        ac=profile["ac"],
        skill_prof={},
        save_prof=[],
        portrait=data.portrait.strip() or "星",
        blurb=f"{blurb}（{archetype}）",
        inventory=["旅行裝備", "火把數支", "水袋", "少量銀幣"],
        actions=[profile["action"]],
    )


@router.get("/auth/discord/login")
async def discord_login(request: Request):
    if not _oauth_ready():
        return JSONResponse({"error": "Discord OAuth is not configured"}, status_code=503)
    state = secrets.token_urlsafe(24)
    request.session["discord_oauth_state"] = state
    params = {
        "client_id": settings.discord_oauth_client_id,
        "redirect_uri": settings.discord_oauth_redirect_uri,
        "response_type": "code",
        "scope": "identify",
        "state": state,
        "prompt": "none",
    }
    return RedirectResponse(f"{DISCORD_AUTH_URL}?{urlencode(params)}")


@router.get("/auth/discord/callback")
async def discord_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    expected = request.session.pop("discord_oauth_state", None)
    if not expected or state != expected:
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    if not code:
        raise HTTPException(status_code=400, detail="Missing OAuth code")
    async with httpx.AsyncClient(timeout=10) as client:
        token_response = await client.post(
            DISCORD_TOKEN_URL,
            data={
                "client_id": settings.discord_oauth_client_id,
                "client_secret": settings.discord_oauth_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.discord_oauth_redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if token_response.status_code >= 400:
            raise HTTPException(status_code=502, detail="Discord token exchange failed")
        token = token_response.json()
        user_response = await client.get(
            f"{DISCORD_API_BASE}/users/@me",
            headers={"Authorization": f"Bearer {token.get('access_token', '')}"},
        )
        if user_response.status_code >= 400:
            raise HTTPException(status_code=502, detail="Discord user lookup failed")
    raw_user = user_response.json()
    request.session["discord_user"] = {
        "id": str(raw_user.get("id")),
        "username": raw_user.get("username") or "",
        "global_name": raw_user.get("global_name") or raw_user.get("username") or "",
        "avatar": raw_user.get("avatar"),
        "authenticated_at": int(time.time()),
    }
    return RedirectResponse(_portal_redirect())


@router.post("/auth/logout")
async def logout(request: Request) -> JSONResponse:
    request.session.pop("discord_user", None)
    return JSONResponse({"ok": True})


@router.get("/me")
async def me(request: Request) -> JSONResponse:
    return JSONResponse(_portal_state(request))


@router.post("/characters/{pc_id}/claim")
async def claim_character(pc_id: str, request: Request) -> JSONResponse:
    user = _require_user(request)
    gs = game_state.get_state()
    if not gs:
        raise HTTPException(status_code=404, detail="No active campaign")
    display_name = user.get("global_name") or user.get("username") or user["id"]
    if not gs.claim_pc(str(user["id"]), pc_id, str(display_name)):
        raise HTTPException(status_code=409, detail="Character is unavailable")
    gs.save()
    return JSONResponse(_portal_state(request))


@router.post("/characters")
async def create_character(payload: CharacterCreateRequest, request: Request) -> JSONResponse:
    user = _require_user(request)
    gs = game_state.get_state()
    if not gs:
        gs = game_state.reset_state(channel_id=0)
        await location_registration.ensure_seed_location_cards(gs, scenario.LOCATIONS)
    pc = _character_from_request(payload, str(user["id"]))
    gs.characters[pc.id] = pc
    gs.pc_ids.append(pc.id)
    display_name = user.get("global_name") or user.get("username") or user["id"]
    gs.claim_pc(str(user["id"]), pc.id, str(display_name))
    gs.add_event(Event(
        actor_id=pc.id,
        actor_name=pc.name,
        kind="character",
        summary=f"{pc.name} 加入了冒險入口。",
    ))
    gs.save()
    return JSONResponse(_portal_state(request), status_code=201)
