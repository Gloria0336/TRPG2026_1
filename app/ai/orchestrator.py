"""OpenRouter-backed intent parsing + narration, with deterministic offline fallbacks.

Layered routing (§8.2): MODEL_INTENT (cheap) parses intent; MODEL_NARRATE (strong)
narrates. If AI is offline / unkeyed / errors out, we fall back to rule-based parsing
and canned narration so a session never hard-stops — and so tests run without network.

Every step is traced through `trpg.ai` so `logs/trace.log` shows exactly which stage
took the fallback path (AI disabled, HTTP failed, JSON malformed, schema rejected …).
"""
from __future__ import annotations

import json
import re
import time

import httpx

from ..config import settings
from ..engine.resolution import APPROACH_SYNONYMS, normalize_approach, requires_check
from ..engine.types import Character, Intent, IntentTier, ResolutionResult, ResultKind, SKILLS
from ..logging_setup import get_logger, truncate
from ..state.game_state import GameState
from . import guard, prompts
from .schemas import EntityExtraction, IntentParse

log = get_logger("ai")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

_ATTACK_WORDS = {"attack", "hit", "strike", "stab", "slash", "shoot", "fight",
                 "kill", "swing", "fire", "smite", "charge", "punch"}

_client: httpx.AsyncClient | None = None
_health_cache: dict | None = None
_health_checked_at: float = 0.0
_HEALTH_TTL_SECONDS = 15.0


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
    enabled = not settings.ai_offline and bool(settings.openrouter_api_key)
    if not enabled:
        log.warning("AI disabled — ai_offline=%s has_key=%s", settings.ai_offline, bool(settings.openrouter_api_key))
    return enabled


def _health_payload(status: str, message: str, latency_ms: int | None = None) -> dict:
    return {
        "status": status,
        "message": message,
        "latency_ms": latency_ms,
        "ai_offline": settings.ai_offline,
        "has_api_key": bool(settings.openrouter_api_key),
        "model_intent": settings.model_intent,
        "model_narrate": settings.model_narrate,
        "checked_at": int(time.time()),
    }


async def health(force: bool = False) -> dict:
    """Lightweight OpenRouter connectivity check for the dashboard."""
    global _health_cache, _health_checked_at
    now = time.monotonic()
    if not force and _health_cache and now - _health_checked_at < _HEALTH_TTL_SECONDS:
        return _health_cache

    if settings.ai_offline:
        _health_cache = _health_payload("offline", "AI offline mode is enabled.")
        _health_checked_at = now
        return _health_cache
    if not settings.openrouter_api_key:
        _health_cache = _health_payload("missing_key", "OPENROUTER_API_KEY is not configured.")
        _health_checked_at = now
        return _health_cache

    started = time.perf_counter()
    try:
        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "HTTP-Referer": settings.openrouter_app_url,
            "X-Title": settings.openrouter_app_name,
        }
        log.debug("health: GET %s", OPENROUTER_MODELS_URL)
        resp = await _http().get(OPENROUTER_MODELS_URL, headers=headers)
        resp.raise_for_status()
        latency_ms = int((time.perf_counter() - started) * 1000)
        log.info("health: online (%d ms)", latency_ms)
        _health_cache = _health_payload("online", "OpenRouter is reachable.", latency_ms)
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.perf_counter() - started) * 1000)
        log.error("health: error after %d ms — %s: %s", latency_ms, type(exc).__name__, exc, exc_info=True)
        _health_cache = _health_payload("error", f"{type(exc).__name__}: {exc}", latency_ms)
    _health_checked_at = now
    return _health_cache


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

    log.info("_chat → POST %s model=%s json_mode=%s max_tokens=%d",
             OPENROUTER_URL, model, json_mode, max_tokens)
    log.debug("_chat system: %s", truncate(system, 400))
    log.debug("_chat user:   %s", truncate(user, 1200))

    started = time.perf_counter()
    try:
        resp = await _http().post(OPENROUTER_URL, headers=headers, json=body)
    except Exception as exc:
        elapsed = int((time.perf_counter() - started) * 1000)
        log.error("_chat HTTP transport failure after %d ms — %s: %s",
                  elapsed, type(exc).__name__, exc, exc_info=True)
        raise

    elapsed = int((time.perf_counter() - started) * 1000)
    log.info("_chat ← status=%s elapsed=%d ms", resp.status_code, elapsed)

    if resp.status_code >= 400:
        log.error("_chat HTTP %s body: %s", resp.status_code, truncate(resp.text, 800))
    resp.raise_for_status()

    try:
        payload = resp.json()
    except Exception as exc:
        log.error("_chat response is not JSON — %s body=%s", type(exc).__name__, truncate(resp.text, 800), exc_info=True)
        raise

    try:
        content = payload["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError) as exc:
        log.error("_chat payload missing choices[0].message.content — payload=%s",
                  truncate(json.dumps(payload, ensure_ascii=False), 800), exc_info=True)
        raise
    log.debug("_chat content: %s", truncate(content, 1200))
    return content


# ───────────────────────── Intent parsing ─────────────────────────
async def interpret(state: GameState, actor_id: str, text: str) -> tuple[Intent, int | None]:
    """Parse a player's message into a structured Intent (+ optional proposed DC)."""
    log.info("interpret() actor=%s text=%r scene=%s", actor_id, text, state.scene.id)
    actor = state.characters.get(actor_id)
    if actor is None:
        log.error("interpret: unknown actor_id=%s known=%s", actor_id, list(state.characters))
        raise KeyError(actor_id)

    if _ai_enabled():
        try:
            log.debug("interpret: calling AI with model=%s", settings.model_intent)
            raw = await _chat(
                settings.model_intent,
                prompts.INTENT_SYSTEM,
                prompts.intent_context(state, actor, text),
                json_mode=True,
                max_tokens=300,
            )
            log.debug("interpret: raw model reply: %s", truncate(raw, 1200))
            extracted = _extract_json(raw)
            log.debug("interpret: extracted JSON: %s", truncate(extracted, 1200))
            parsed = IntentParse.model_validate_json(extracted)
            log.info("interpret: AI parse OK tier=%s action=%s target=%s approach=%s is_attack=%s dc=%s",
                     parsed.tier, parsed.action, parsed.target, parsed.approach,
                     parsed.is_attack, parsed.snapped_dc())
            intent = _to_intent(actor_id, text, parsed)
            _apply_check_gate(state, intent)
            log.debug("interpret: built Intent=%r", intent)
            return intent, parsed.snapped_dc()
        except httpx.HTTPStatusError as exc:
            log.warning("interpret: AI HTTP %s — falling back to offline parse. body=%s",
                        exc.response.status_code, truncate(exc.response.text, 600))
        except httpx.HTTPError as exc:
            log.warning("interpret: AI transport failure (%s: %s) — falling back to offline parse.",
                        type(exc).__name__, exc)
        except Exception as exc:
            # Schema, JSON, or unexpected error — log full traceback so root cause is visible.
            log.error("interpret: AI fallback via %s: %s: %s",
                      settings.model_intent, type(exc).__name__, exc, exc_info=True)
    else:
        log.info("interpret: AI disabled, using offline parser")

    fallback = _offline_parse(state, actor, text)
    _apply_check_gate(state, fallback)
    log.info("interpret: offline parse → tier=%s action=%s target=%s approach=%s is_attack=%s",
             fallback.tier.value, fallback.action, fallback.target, fallback.approach, fallback.is_attack)
    return fallback, None


def _apply_check_gate(state: GameState, intent: Intent) -> None:
    """Engine has the final say on whether a Tier-A action rolls (design §8.3). The AI
    may propose `needs_check=false`, but `requires_check` can force it back to true so a
    contested/risky/targeted action can never be narrated into a free success. Final
    value = AI proposal OR engine requirement."""
    final = bool(intent.needs_check or requires_check(state, intent))
    if final != intent.needs_check:
        log.info("interpret: check-gate override needs_check %s→%s (action=%s target=%s approach=%s)",
                 intent.needs_check, final, intent.action, intent.target, intent.approach)
    intent.needs_check = final


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
        needs_check=p.needs_check,
        candidates=p.candidates,
        question=p.question,
        options=p.options,
        implausible=p.implausible,
    )


def _offline_parse(state: GameState, actor: Character, text: str) -> Intent:
    """Deterministic, network-free intent parser (fallback + tests + smoke)."""
    low = text.lower()
    tokens = re.findall(r"[a-z_]+", low)

    is_attack = any(t in _ATTACK_WORDS for t in tokens)

    approach = None
    for t in tokens:
        if t in SKILLS or t in APPROACH_SYNONYMS:
            approach = normalize_approach(t)
            break

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

    if len(tokens) >= 2:
        return Intent(
            actor_id=actor.id, raw_text=text, tier=IntentTier.B, target=target,
            candidates=["investigate it (Investigation)", "look closer (Perception)",
                        "talk it out (Persuasion)", "force the issue (Athletics)"],
        )
    return Intent(
        actor_id=actor.id, raw_text=text, tier=IntentTier.C,
        question="What would you like to do?",
        options=["Look around", "Talk to someone", "Examine something", "Move on"],
    )


# ───────────────────────── Narration ─────────────────────────
async def narrate(state: GameState, result: ResolutionResult) -> str:
    """Turn a computed ResolutionResult into prose. Never alters the result."""
    log.info("narrate() kind=%s actor=%s summary=%s",
             getattr(result, "kind", "?"), result.actor_name, truncate(result.summary, 200))
    # A no-roll narrative beat has no numbers to dramatize or protect — narrate it with
    # a lighter "nothing is at stake" system prompt and skip the number guard.
    is_beat = getattr(result, "kind", None) is ResultKind.NARRATIVE
    narrate_system = prompts.NARRATE_BEAT_SYSTEM if is_beat else prompts.NARRATE_SYSTEM
    if _ai_enabled():
        try:
            base_user = prompts.narrate_context(state, result)
            prose = await _chat(
                settings.model_narrate,
                narrate_system,
                base_user,
                max_tokens=200,
            )
            log.info("narrate: AI narration OK (%d chars)", len(prose))
            log.debug("narrate: prose=%s", truncate(prose, 500))

            # §8.0 guard: enforce "AI never touches numbers" at the engine boundary.
            # On violation, give the model one strict-reminder retry before bailing.
            # A beat carries no numbers, so there is nothing to guard.
            violations = [] if is_beat else guard.find_violations(prose, result)
            if violations:
                log.warning("narrate: guard caught violations on first reply: %s",
                            "; ".join(violations))
                retry_user = base_user + "\n\n" + guard.violation_reminder(violations)
                try:
                    retry = await _chat(
                        settings.model_narrate,
                        narrate_system,
                        retry_user,
                        max_tokens=200,
                    )
                    log.info("narrate: AI narration retry OK (%d chars)", len(retry))
                    log.debug("narrate: retry prose=%s", truncate(retry, 500))
                    retry_violations = guard.find_violations(retry, result)
                    if not retry_violations:
                        return retry
                    log.error("narrate: guard rejected retry too (%s) — using canned",
                              "; ".join(retry_violations))
                except Exception as exc:
                    log.error("narrate: retry failed (%s: %s) — using canned",
                              type(exc).__name__, exc, exc_info=True)
                # Fall through to canned narration when retry also fails the guard.
            else:
                return prose
        except httpx.HTTPStatusError as exc:
            log.warning("narrate: AI HTTP %s — using canned narration. body=%s",
                        exc.response.status_code, truncate(exc.response.text, 600))
        except httpx.HTTPError as exc:
            log.warning("narrate: AI transport failure (%s: %s) — using canned narration.",
                        type(exc).__name__, exc)
        except Exception as exc:
            log.error("narrate: fallback via %s: %s: %s",
                      settings.model_narrate, type(exc).__name__, exc, exc_info=True)
    else:
        log.info("narrate: AI disabled, using canned narration")

    canned = _canned_narration(result)
    log.info("narrate: canned narration (%d chars)", len(canned))
    log.debug("narrate: canned=%s", truncate(canned, 500))
    return canned


def _canned_narration(result: ResolutionResult) -> str:
    """Template narration used offline — flavour from the engine's own hint/deltas."""
    bits = [_fallback_hint_text(result.narration_hint) if result.narration_hint else result.summary]
    if result.deltas:
        bits.append(" ".join(result.deltas))
    return " ".join(b for b in bits if b)


def _fallback_hint_text(hint: str) -> str:
    hints = {
        "Describe a decisive, lucky break.": "出現了決定性的幸運轉機。",
        "Describe an unlucky complication.": "事情突然變糟，出現了麻煩的意外。",
        "Describe a clean success.": "行動俐落成功，沒有額外波折。",
        "Describe a setback; the attempt fails.": "嘗試失敗，局勢出現挫折。",
        "Describe the moment vividly but briefly.": "場面短暫而鮮明地展開。",
        "A tactical maneuver.": "這是一個戰術動作。",
        "Land a solid, cinematic blow.": "攻擊紮實命中，場面俐落有力。",
        "The attack misses or is turned aside.": "攻擊落空，或被對手巧妙化解。",
        "Magic flares against the target.": "魔法在目標身上爆發出光芒。",
        "The target resists the worst of it.": "目標撐過了最嚴重的影響。",
        "Unerring magic strikes home.": "精準的魔法直擊目標。",
        "Warm restorative energy mends wounds.": "溫暖的恢復能量縫合了傷口。",
        "A fragile, fading moment between life and death.": "生死之間只剩下一線微弱的呼吸。",
    }
    return hints.get(hint, hint)


async def extract_entity_states(state: GameState, prose: str, result: ResolutionResult | None = None) -> EntityExtraction:
    """Read a narration and pull validated entity-state deltas (who left, who turned
    hostile, who newly appeared). Cheap model, JSON mode. Offline / disabled / on any
    error → empty extraction, so only structured/engine deltas apply (design fallback)."""
    if not prose or not prose.strip():
        return EntityExtraction()
    if not (_ai_enabled() and settings.entity_extraction_enabled):
        log.info("extract: skipped (ai_enabled=%s flag=%s)",
                 _ai_enabled(), settings.entity_extraction_enabled)
        return EntityExtraction()
    try:
        raw = await _chat(
            settings.model_extract,
            prompts.EXTRACT_SYSTEM,
            prompts.extract_context(state, prose),
            json_mode=True,
            max_tokens=300,
        )
        extracted = _extract_json(raw)
        parsed = EntityExtraction.model_validate_json(extracted)
        log.info("extract: %d actionable delta(s) of %d", len(parsed.actionable()), len(parsed.deltas))
        log.debug("extract: deltas=%s", truncate(str(parsed.deltas), 500))
        return parsed
    except httpx.HTTPError as exc:
        log.warning("extract: transport/HTTP failure (%s: %s) — no deltas", type(exc).__name__, exc)
    except Exception as exc:  # noqa: BLE001 — schema/JSON error must not break play
        log.warning("extract: failed (%s: %s) — no deltas", type(exc).__name__, exc)
    return EntityExtraction()


async def open_scene(state: GameState) -> str:
    log.info("open_scene() scene=%s title=%s", state.scene.id, state.scene.title)
    if _ai_enabled():
        try:
            prose = await _chat(
                settings.model_narrate, prompts.SCENE_SYSTEM, prompts.scene_context(state), max_tokens=220
            )
            log.info("open_scene: AI OK (%d chars)", len(prose))
            return prose
        except httpx.HTTPStatusError as exc:
            log.warning("open_scene: AI HTTP %s — using scene summary. body=%s",
                        exc.response.status_code, truncate(exc.response.text, 600))
        except httpx.HTTPError as exc:
            log.warning("open_scene: AI transport failure (%s: %s) — using scene summary.",
                        type(exc).__name__, exc)
        except Exception as exc:
            log.error("open_scene: fallback via %s: %s: %s",
                      settings.model_narrate, type(exc).__name__, exc, exc_info=True)
    else:
        log.info("open_scene: AI disabled, using scene summary")
    return state.scene.summary
