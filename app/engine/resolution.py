"""Resolution pipeline — turns a structured Intent into a ResolutionResult.

This is the out-of-combat path (skill/ability checks and narrative beats). Combat
actions are routed through combat.py. Either way the engine owns every number;
the AI only supplies the Intent slots beforehand and the prose afterwards.

Design refs: §4.5 (DC ownership: scene table first, AI proposal snapped to anchors),
§4.6 (pipeline → structured result → event_log), §8.3 (intent already structured).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import dice, rules_5e
from .types import (
    Character,
    Cost,
    CostSeverity,
    CostType,
    Intent,
    ResolutionResult,
    ResultBand,
    ResultKind,
    SKILLS,
)
from ..logging_setup import get_logger

if TYPE_CHECKING:
    from ..state.game_state import GameState

log = get_logger("resolution")

# Map common free-text verbs to a governing 5e skill so the engine is robust even
# when the AI hands us a loose `approach`.
APPROACH_SYNONYMS: dict[str, str] = {
    "persuade": "persuasion", "convince": "persuasion", "talk": "persuasion", "negotiate": "persuasion",
    "intimidate": "intimidation", "threaten": "intimidation", "menace": "intimidation",
    "deceive": "deception", "lie": "deception", "bluff": "deception", "trick": "deception",
    "sneak": "stealth", "hide": "stealth", "skulk": "stealth",
    "climb": "athletics", "jump": "athletics", "swim": "athletics", "force": "athletics",
    "break": "athletics", "shove": "athletics", "grapple": "athletics", "lift": "athletics",
    "lockpick": "sleight_of_hand", "pick_lock": "sleight_of_hand", "pick": "sleight_of_hand",
    "disarm": "sleight_of_hand", "pickpocket": "sleight_of_hand", "palm": "sleight_of_hand",
    "tumble": "acrobatics", "balance": "acrobatics", "flip": "acrobatics",
    "search": "investigation", "investigate": "investigation", "examine": "investigation",
    "notice": "perception", "spot": "perception", "listen": "perception", "look": "perception", "scan": "perception",
    "recall": "arcana", "identify": "arcana",
    "track": "survival", "forage": "survival", "navigate": "survival",
    "heal": "medicine", "first_aid": "medicine", "diagnose": "medicine",
    "read": "insight", "sense": "insight",
    "command": "persuasion", "perform": "performance", "sing": "performance", "play": "performance",
    "recall_lore": "history", "history": "history", "religion": "religion", "nature": "nature",
    "tame": "animal_handling", "calm": "animal_handling",
}


def normalize_approach(approach: str | None) -> str:
    """Best-effort map an approach string to a 5e skill key."""
    if not approach:
        return "improvise"
    a = approach.strip().lower().replace(" ", "_")
    if a in SKILLS:
        return a
    if a in APPROACH_SYNONYMS:
        return APPROACH_SYNONYMS[a]
    # Sometimes the AI hands us a full phrase; scan tokens for a known verb/skill.
    for token in a.split("_"):
        if token in SKILLS:
            return token
        if token in APPROACH_SYNONYMS:
            return APPROACH_SYNONYMS[token]
    return approach  # let check_bonus fall back to flat/ability handling


# Default skill → CostType fallback when a scene has no explicit cost_pool (§4.7).
# Chosen so the fiction has a sensible "what went wrong" hook even with bare scenes.
_SKILL_DEFAULT_COST: dict[str, CostType] = {
    "stealth": CostType.EXPOSURE,
    "deception": CostType.EXPOSURE,
    "sleight_of_hand": CostType.TRACE,
    "persuasion": CostType.RELATION,
    "intimidation": CostType.RELATION,
    "performance": CostType.RELATION,
    "insight": CostType.RELATION,
    "athletics": CostType.RESOURCE,
    "acrobatics": CostType.RESOURCE,
    "medicine": CostType.RESOURCE,
    "investigation": CostType.TIME,
    "perception": CostType.TIME,
    "survival": CostType.TIME,
    "arcana": CostType.TIME,
    "history": CostType.TIME,
    "religion": CostType.TIME,
    "nature": CostType.TIME,
    "animal_handling": CostType.ATTENTION,
}

_DEFAULT_COST: CostType = CostType.TIME

_COST_NOTE_ZH: dict[CostType, str] = {
    CostType.TIME: "時間流逝",
    CostType.EXPOSURE: "行蹤暴露",
    CostType.RESOURCE: "資源損耗",
    CostType.TRACE: "留下痕跡",
    CostType.ATTENTION: "引來注意",
    CostType.RELATION: "關係惡化",
    CostType.DEBT: "延遲後果",
}

_SEVERITY_NOTE_ZH: dict[CostSeverity, str] = {
    CostSeverity.LIGHT: "輕微",
    CostSeverity.MODERATE: "中等",
    CostSeverity.HEAVY: "嚴重",
}


def _severity_for_band(band: ResultBand, *, fumble: bool) -> CostSeverity:
    """Map band → cost severity (§4.4 + §4.7).

    PARTIAL leans light/moderate, FAILURE leans moderate/heavy. A nat-1 fumble
    that ended in FAILURE bumps to heavy regardless.
    """
    if band is ResultBand.FAILURE:
        return CostSeverity.HEAVY if fumble else CostSeverity.MODERATE
    if band is ResultBand.PARTIAL:
        return CostSeverity.MODERATE if fumble else CostSeverity.LIGHT
    return CostSeverity.LIGHT


def pick_cost(state: "GameState", skill: str, band: ResultBand, *, fumble: bool = False) -> Cost | None:
    """Pick a structured Cost for a PARTIAL/FAILURE check (§4.7).

    Selection order: scene.cost_pool (if any) → skill default → TIME. Sampling uses
    the dice RNG so the same seed gives the same costs in tests.
    """
    if band is ResultBand.SUCCESS:
        return None

    pool = list(getattr(state.scene, "cost_pool", []) or [])
    cost_type: CostType | None = None
    if pool:
        try:
            cost_type = CostType(dice.choice(pool))
        except ValueError:
            # Tolerate typos in scenario data without crashing the engine.
            log.warning("pick_cost: scene %s has invalid cost_pool entry; falling back", state.scene.id)
            cost_type = None
    if cost_type is None:
        cost_type = _SKILL_DEFAULT_COST.get(skill, _DEFAULT_COST)

    severity = _severity_for_band(band, fumble=fumble)
    note = f"{_SEVERITY_NOTE_ZH[severity]}的{_COST_NOTE_ZH[cost_type]}"
    log.info("pick_cost: skill=%s band=%s → type=%s severity=%s", skill, band.value, cost_type.value, severity.value)
    return Cost(type=cost_type, severity=severity, persistent=False, note=note)


def determine_dc(state: "GameState", intent: Intent, proposed_dc: int | None) -> int:
    """DC ownership (§4.5): scene challenge table first, then AI proposal (snapped to
    a 5e anchor), then a default medium DC."""
    challenges = state.scene.challenges or {}
    skill = normalize_approach(intent.approach)
    for key in (skill, intent.approach, intent.action):
        if key and str(key).lower() in challenges:
            dc = int(challenges[str(key).lower()])
            log.debug("determine_dc: matched scene challenge key=%s → DC %d", key, dc)
            return dc
    if proposed_dc is not None:
        dc = rules_5e.nearest_anchor(int(proposed_dc))
        log.debug("determine_dc: snapped AI-proposed %s → DC %d", proposed_dc, dc)
        return dc
    log.debug("determine_dc: defaulting to DC 12 (no scene match, no AI proposal)")
    return 12


def _compose_external(
    state: "GameState",
    actor: Character,
    skill: str,
    helpers: list[str] | None,
    env_tier: int,
    tool_bonus: int,
    resource_spend: bool,
) -> tuple[int, list[str]]:
    """Combine §4.9 external sources into one capped offset + a human-readable breakdown.

    Returns (capped_bonus, parts). The parts list is for the embed/log so a player
    can see *why* the modifier landed where it did — assist from whom, env tier, etc.
    """
    parts: list[str] = []
    assist_pts = 0
    if helpers:
        helper_chars = [
            state.characters[h] for h in helpers
            if h in state.characters and h != actor.id and not state.characters[h].is_down
        ]
        assist_pts = rules_5e.assist_bonus(helper_chars, skill)
        if assist_pts:
            names = "、".join(h.name for h in helper_chars if h.skill_prof.get(skill) in ("prof", "expertise"))
            parts.append(f"協助 +{assist_pts}（{names}）")
        elif helper_chars:
            # Surface that someone tried to help but didn't qualify — useful for UX
            # so the player isn't confused about why "I help" gave +0.
            parts.append("協助 +0（無受訓夥伴）")
    if env_tier:
        sign = "+" if env_tier > 0 else ""
        parts.append(f"環境 {sign}{env_tier}")
    if tool_bonus:
        sign = "+" if tool_bonus > 0 else ""
        parts.append(f"工具 {sign}{tool_bonus}")
    resource_pts = rules_5e.RESOURCE_SPEND_BONUS if resource_spend else 0
    if resource_pts:
        parts.append(f"消耗資源 +{resource_pts}")

    raw = assist_pts + env_tier + tool_bonus + resource_pts
    capped = rules_5e.cap_external(raw)
    if capped != raw:
        parts.append(f"外部總封頂 → {'+' if capped >= 0 else ''}{capped}")
    return capped, parts


def resolve(
    state: "GameState",
    intent: Intent,
    *,
    proposed_dc: int | None = None,
    helpers: list[str] | None = None,
    env_tier: int = 0,
    tool_bonus: int = 0,
    resource_spend: bool = False,
) -> ResolutionResult:
    """Resolve an out-of-combat Tier-A intent into a ResolutionResult and log it.

    `helpers`, `env_tier`, `tool_bonus`, `resource_spend` plug into §4.9 external
    bonuses. The engine combines and caps them; AI never picks any of these numbers.
    """
    actor = state.characters.get(intent.actor_id)
    if actor is None:
        log.error("resolve: unknown actor=%s known=%s", intent.actor_id, list(state.characters))
        raise KeyError(f"Unknown actor: {intent.actor_id}")

    skill = normalize_approach(intent.approach or intent.action)
    dc = determine_dc(state, intent, proposed_dc)
    advantage, disadvantage = state.scene.advantage_for(skill)

    external_bonus, external_parts = _compose_external(
        state, actor, skill, helpers, env_tier, tool_bonus, resource_spend
    )

    log.info("resolve: actor=%s skill=%s dc=%s adv=%s disadv=%s external=%+d (%s)",
             actor.name, skill, dc, advantage, disadvantage, external_bonus,
             ", ".join(external_parts) or "none")

    result = rules_5e.ability_check(
        actor, skill, dc,
        advantage=advantage,
        disadvantage=disadvantage,
        external_bonus=external_bonus,
    )
    if intent.target:
        result.target_name = intent.target
    # Pass the player's original utterance through so the narrator sees who they
    # actually addressed — otherwise it has to guess from the scene description.
    if intent.raw_text:
        result.raw_text = intent.raw_text

    if external_parts:
        result.deltas.append("外部加值：" + "、".join(external_parts))

    # Structured cost on PARTIAL/FAILURE (§4.7). Recorded into deltas so the dashboard
    # log + future RAG/history layer can read it as plain text without re-parsing JSON.
    if result.band and result.band is not ResultBand.SUCCESS:
        cost = pick_cost(state, skill, result.band, fumble=result.fumble)
        if cost is not None:
            result.cost = cost
            result.deltas.append(f"代價：{cost.note}")

    log.info("resolve: result band=%s success=%s roll=%s summary=%s target=%s cost=%s",
             result.band.value if result.band else None,
             result.success, result.roll_breakdown, result.summary, result.target_name,
             result.cost.type.value if result.cost else None)
    state.log_result(result)
    return result


def narrative_beat(state: "GameState", actor: Character, summary: str, hint: str = "") -> ResolutionResult:
    """A no-roll beat (utility action, scene transition). Still produces a logged result."""
    result = ResolutionResult(
        kind=ResultKind.NARRATIVE,
        actor_id=actor.id,
        actor_name=actor.name,
        summary=summary,
        narration_hint=hint or "Describe the moment vividly but briefly.",
    )
    state.log_result(result)
    return result
