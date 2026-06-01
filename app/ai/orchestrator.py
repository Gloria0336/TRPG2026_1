"""OpenRouter-backed intent parsing + narration, with deterministic offline fallbacks.

Layered routing (§8.2): MODEL_INTENT (cheap) parses intent; MODEL_NARRATE (strong)
narrates. If AI is offline / unkeyed / errors out, we fall back to rule-based parsing
and canned narration so a session never hard-stops — and so tests run without network.
"""
from __future__ import annotations

import json
import re

import httpx

from ..config import settings
from ..engine.resolution import APPROACH_SYNONYMS, normalize_approach
from ..engine.types import Character, Intent, IntentTier, ResolutionResult, SKILLS
from ..state.game_state import GameState
from . import prompts
from .schemas import IntentParse

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_ATTACK_WORDS = {"attack", "hit", "strike", "stab", "slash", "shoot", "fight",
                 "kill", "swing", "fire", "smite", "charge", "punch"}

_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _ai_enabled() -> bool:
    return not settings.ai_offline and bool(settings.openrouter_api_key)


async def _chat(model: str, system: str, user: str, *, json_mode: bool = False, max_tokens: int = 400) -> str:
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": settings.openrouter_app_url,
        "X-Title": settings.openrouter_app_name,
        "Content-Type": "application/json",
    }
    body: dict = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.2 if json_mode else 0.8,
        "max_tokens": max_tokens,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    resp = await _http().post(OPENROUTER_URL, headers=headers, json=body)
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


# ───────────────────────── Intent parsing ─────────────────────────
async def interpret(state: GameState, actor_id: str, text: str) -> tuple[Intent, int | None]:
    """Parse a player's message into a structured Intent (+ optional proposed DC)."""
    actor = state.characters.get(actor_id)
    if actor is None:
        raise KeyError(actor_id)

    if _ai_enabled():
        try:
            raw = await _chat(
                settings.model_intent,
                prompts.INTENT_SYSTEM,
                prompts.intent_context(state, actor, text),
                json_mode=True,
                max_tokens=300,
            )
            parsed = IntentParse.model_validate_json(_extract_json(raw))
            return _to_intent(actor_id, text, parsed), parsed.snapped_dc()
        except Exception:
            # Any failure (network, schema, parse) → safe rule-based fallback.
            pass
    return _offline_parse(state, actor, text), None


def _extract_json(raw: str) -> str:
    """Pull the first JSON object out of a possibly fenced/chatty reply."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):]
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        return raw[start : end + 1]
    return raw


def _to_intent(actor_id: str, text: str, p: IntentParse) -> Intent:
    return Intent(
        actor_id=actor_id,
        raw_text=text,
        tier=IntentTier(p.tier),
        action=p.action,
        target=p.target,
        approach=normalize_approach(p.approach) if p.approach else None,
        is_attack=p.is_attack,
        candidates=p.candidates,
        question=p.question,
        options=p.options,
    )


def _offline_parse(state: GameState, actor: Character, text: str) -> Intent:
    """Deterministic, network-free intent parser (fallback + tests + smoke)."""
    low = text.lower()
    tokens = re.findall(r"[a-z_]+", low)

    is_attack = any(t in _ATTACK_WORDS for t in tokens)

    # Find a skill/verb.
    approach = None
    for t in tokens:
        if t in SKILLS or t in APPROACH_SYNONYMS:
            approach = normalize_approach(t)
            break

    # Guess a target from NPCs present or scene mentions.
    target = None
    for c in state.characters.values():
        if not c.is_pc and c.name.lower().split()[0] in low:
            target = c.name
            break
    if target is None:
        for npc in state.scene.npcs:
            first = re.sub(r"[^a-z]", "", npc.lower().split()[0])
            if first and first in low:
                target = npc
                break

    if is_attack:
        return Intent(actor_id=actor.id, raw_text=text, tier=IntentTier.A,
                      action="attack", target=target, approach=approach, is_attack=True)
    if approach:
        return Intent(actor_id=actor.id, raw_text=text, tier=IntentTier.A,
                      action=approach, target=target, approach=approach)

    # No clear method but some content → tier B with generic candidates.
    if len(tokens) >= 2:
        return Intent(
            actor_id=actor.id, raw_text=text, tier=IntentTier.B, target=target,
            candidates=["investigate it (Investigation)", "look closer (Perception)",
                        "talk it out (Persuasion)", "force the issue (Athletics)"],
        )
    # Too vague → tier C.
    return Intent(
        actor_id=actor.id, raw_text=text, tier=IntentTier.C,
        question="What would you like to do?",
        options=["Look around", "Talk to someone", "Examine something", "Move on"],
    )


# ───────────────────────── Narration ─────────────────────────
async def narrate(state: GameState, result: ResolutionResult) -> str:
    """Turn a computed ResolutionResult into prose. Never alters the result."""
    if _ai_enabled():
        try:
            return await _chat(
                settings.model_narrate,
                prompts.NARRATE_SYSTEM,
                prompts.narrate_context(state, result),
                max_tokens=200,
            )
        except Exception:
            pass
    return _canned_narration(result)


def _canned_narration(result: ResolutionResult) -> str:
    """Template narration used offline — flavour from the engine's own hint/deltas."""
    bits = [result.narration_hint or result.summary]
    if result.deltas:
        bits.append(" ".join(result.deltas))
    return " ".join(b for b in bits if b)


async def open_scene(state: GameState) -> str:
    if _ai_enabled():
        try:
            return await _chat(
                settings.model_narrate, prompts.SCENE_SYSTEM, prompts.scene_context(state), max_tokens=220
            )
        except Exception:
            pass
    return state.scene.summary
