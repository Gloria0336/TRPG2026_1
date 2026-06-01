"""Resolution pipeline — turns a structured Intent into a ResolutionResult.

This is the out-of-combat path (skill/ability checks and narrative beats). Combat
actions are routed through combat.py. Either way the engine owns every number;
the AI only supplies the Intent slots beforehand and the prose afterwards.

Design refs: §4.5 (DC ownership: scene table first, AI proposal snapped to anchors),
§4.6 (pipeline → structured result → event_log), §8.3 (intent already structured).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import rules_5e
from .types import Character, Intent, ResolutionResult, ResultKind, SKILLS
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


def resolve(state: "GameState", intent: Intent, *, proposed_dc: int | None = None) -> ResolutionResult:
    """Resolve an out-of-combat Tier-A intent into a ResolutionResult and log it."""
    actor = state.characters.get(intent.actor_id)
    if actor is None:
        log.error("resolve: unknown actor=%s known=%s", intent.actor_id, list(state.characters))
        raise KeyError(f"Unknown actor: {intent.actor_id}")

    skill = normalize_approach(intent.approach or intent.action)
    dc = determine_dc(state, intent, proposed_dc)
    advantage, disadvantage = state.scene.advantage_for(skill)
    log.info("resolve: actor=%s skill=%s dc=%s adv=%s disadv=%s",
             actor.name, skill, dc, advantage, disadvantage)

    result = rules_5e.ability_check(actor, skill, dc, advantage=advantage, disadvantage=disadvantage)
    if intent.target:
        result.target_name = intent.target

    log.info("resolve: result success=%s roll=%s summary=%s",
             result.success, result.roll_breakdown, result.summary)
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
