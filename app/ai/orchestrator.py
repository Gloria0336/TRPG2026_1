"""OpenRouter-backed intent parsing + narration, with deterministic offline fallbacks.

Layered routing (§8.2): MODEL_INTENT (cheap) parses intent; MODEL_NARRATE (strong)
narrates. If AI is offline / unkeyed / errors out, we fall back to rule-based parsing
and canned narration so a session never hard-stops — and so tests run without network.

Every step is traced through `trpg.ai` so the active `logs/trace_*.log` shows exactly which stage
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
from .schemas import (
    DCAssessment,
    EntityExtraction,
    IntentParse,
    LocationCard,
    NarrationQuestEnvelope,
    QuestDetails,
    QuestSeed,
)

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
async def interpret(
    state: GameState,
    actor_id: str,
    text: str,
    *,
    clarification: list[dict] | None = None,
) -> tuple[Intent, DCAssessment | None]:
    """Parse a player's message into a structured Intent (+ optional DC assessment).

    `clarification` is the open follow-up history for this actor (the prior GM
    questions + the player's replies). The parser sees it and is expected to
    converge toward tier A rather than re-ask the same question.
    """
    log.info("interpret() actor=%s text=%r scene=%s clarification_turns=%d",
             actor_id, text, state.scene.id, len(clarification or []))
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
                prompts.intent_context(state, actor, text, clarification=clarification),
                json_mode=True,
                max_tokens=300,
            )
            log.debug("interpret: raw model reply: %s", truncate(raw, 1200))
            extracted = _extract_json(raw)
            log.debug("interpret: extracted JSON: %s", truncate(extracted, 1200))
            parsed = IntentParse.model_validate_json(extracted)
            assessment = parsed.dc_assessment()
            log.info("interpret: AI parse OK tier=%s action=%s target=%s approach=%s is_attack=%s dc=%s",
                     parsed.tier, parsed.action, parsed.target, parsed.approach,
                     parsed.is_attack, assessment.final_dc if assessment else None)
            intent = _to_intent(actor_id, text, parsed)
            _apply_check_gate(state, intent)
            log.debug("interpret: built Intent=%r", intent)
            return intent, assessment
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
        topic=p.topic,
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
                max_tokens=500,
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
                        max_tokens=500,
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


def _quest_eligible(result: ResolutionResult) -> bool:
    if getattr(result, "kind", None) not in (ResultKind.CHECK, ResultKind.NARRATIVE):
        return False
    if result.success is False:
        return False
    if not result.target_name:
        return False
    text = " ".join(str(x or "") for x in (result.raw_text, result.topic, result.summary)).lower()
    socialish = (
        "persuasion" in text or "insight" in text or "deception" in text
        or "intimidation" in text or "詢問" in text or "委託" in text
        or "任務" in text or "幫" in text or "協助" in text
    )
    return socialish


async def narrate_with_quest(state: GameState, result: ResolutionResult) -> tuple[str, QuestSeed | None]:
    """Narrate a result and let the GM emit a compact quest seed in the same reply."""
    if not _quest_eligible(result):
        return await narrate(state, result), None
    if not _ai_enabled():
        return await narrate(state, result), None

    base_user = prompts.narrate_context(state, result)
    try:
        raw = await _chat(
            settings.model_narrate,
            prompts.NARRATE_QUEST_SYSTEM,
            base_user,
            json_mode=True,
            max_tokens=1200,
        )
        parsed = NarrationQuestEnvelope.model_validate_json(_extract_json(raw))
        prose = (parsed.prose or "").strip()
        if not prose:
            raise ValueError("quest narration payload had empty prose")
        violations = [] if result.kind is ResultKind.NARRATIVE else guard.find_violations(prose, result)
        if violations:
            log.warning("narrate_with_quest: guard rejected prose: %s", "; ".join(violations))
            return await narrate(state, result), None
        seed = parsed.quest_offer if parsed.quest_offer and parsed.quest_offer.should_create() else None
        return prose, seed
    except Exception as exc:  # noqa: BLE001
        log.warning("narrate_with_quest: failed (%s: %s) — falling back to prose-only",
                    type(exc).__name__, exc)
        return await narrate(state, result), None


async def build_quest_details(state: GameState, seed: QuestSeed | dict) -> tuple[dict, str]:
    """Run the quest agent. Returns (details, detail_state)."""
    seed_obj = seed if isinstance(seed, QuestSeed) else QuestSeed.model_validate(seed)
    seed_data = seed_obj.model_dump()
    if _ai_enabled():
        try:
            raw = await _chat(
                settings.model_extract,
                prompts.QUEST_AGENT_SYSTEM,
                prompts.quest_agent_context(state, seed_data),
                json_mode=True,
                max_tokens=800,
            )
            details = QuestDetails.model_validate_json(_extract_json(raw))
            data = details.model_dump()
            if not data.get("title"):
                data["title"] = seed_obj.title_hint
            if not data.get("giver"):
                data["giver"] = seed_obj.giver
            return data, "ready"
        except Exception as exc:  # noqa: BLE001
            log.warning("quest_agent: failed (%s: %s) — using degraded seed details",
                        type(exc).__name__, exc)
    from ..db import store
    return store.fallback_quest_details(seed_data), "details_degraded"


def _fallback_location_card(request: dict) -> LocationCard:
    name = (
        request.get("canonical_name")
        or request.get("requested_name")
        or "未命名地點"
    )
    aliases = [a for a in (request.get("aliases") or []) if isinstance(a, str) and a.strip()]
    notes = (request.get("authored_notes") or "").strip()
    if notes:
        base_summary = notes
    else:
        base_summary = (
            f"{name} 是隊伍剛確立的地點，輪廓仍帶著探索中的不確定感。"
            "這裡的光線、氣味與可觸碰的地形會成為後續描述的固定錨點，"
            "直到玩家行動揭露更多細節。"
        )
    return LocationCard(
        canonical_name=name,
        aliases=aliases,
        base_summary=base_summary,
        sensory_anchors=["潮濕空氣", "低回聲", "不穩定的光線"],
        visual_landmarks=[f"{name}的入口", "可辨認的路徑邊界"],
        interactive_features=["地面痕跡", "牆面或路旁可檢視的細節"],
        discoverables=["此地與來路之間的關聯線索"],
        hazards=["視線受阻或地勢不明帶來的風險"],
        soft_hooks=["可以停下觀察、聆聽，或沿著最明顯的路徑前進"],
        exits_hint=list(request.get("connects") or []),
        mood="未知、緊繃、可探索",
    )


async def build_location_card(request: dict) -> tuple[LocationCard, str]:
    """Run the location registration agent. Always returns a usable card."""
    if _ai_enabled():
        try:
            raw = await _chat(
                settings.model_narrate,
                prompts.LOCATION_CARD_SYSTEM,
                prompts.location_card_context(request),
                json_mode=True,
                max_tokens=1000,
            )
            card = LocationCard.model_validate_json(_extract_json(raw))
            if not card.canonical_name:
                card.canonical_name = request.get("canonical_name") or request.get("requested_name") or ""
            if not card.base_summary:
                card.base_summary = _fallback_location_card(request).base_summary
            log.info(
                "location_card: AI OK name=%s source=%s",
                card.canonical_name,
                request.get("source"),
            )
            return card, "ready"
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "location_card: failed (%s: %s) - using fallback",
                type(exc).__name__,
                exc,
            )
    else:
        log.info("location_card: AI disabled, using fallback")
    return _fallback_location_card(request), "fallback"


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


async def recap_scene(state: GameState) -> str:
    """Fresh GM description of the CURRENT situation from live state (for /scene), not the
    static authored summary. Offline / on any error → the live composed summary text, so
    /scene still reflects who is actually present and what has changed."""
    log.info("recap_scene() location=%s", state.current_location_id)
    if _ai_enabled():
        try:
            prose = await _chat(
                settings.model_narrate, prompts.SCENE_RECAP_SYSTEM,
                prompts.scene_recap_context(state), max_tokens=600,
            )
            log.info("recap_scene: AI OK (%d chars)", len(prose))
            return prose
        except httpx.HTTPError as exc:
            log.warning("recap_scene: HTTP failure (%s: %s) — using live summary",
                        type(exc).__name__, exc)
        except Exception as exc:  # noqa: BLE001
            log.error("recap_scene: fallback (%s: %s)", type(exc).__name__, exc, exc_info=True)
    return prompts.compose_scene_summary(state)


async def open_scene(state: GameState) -> str:
    log.info("open_scene() scene=%s title=%s", state.scene.id, state.scene.title)
    if _ai_enabled():
        try:
            prose = await _chat(
                settings.model_narrate, prompts.SCENE_SYSTEM, prompts.scene_context(state), max_tokens=800
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
        log.info("open_scene: AI disabled, using live composed summary")
    # Offline / on error → the LIVE composed summary (authored seed + present entities),
    # never the raw static blurb, so the opening still reflects real state.
    return prompts.compose_scene_summary(state)
