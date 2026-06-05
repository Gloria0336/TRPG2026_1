"""Resolution pipeline — turns a structured Intent into a ResolutionResult.

This is the out-of-combat path (skill/ability checks and narrative beats). Combat
actions are routed through combat.py. Either way the engine owns every number;
the AI only supplies the Intent slots beforehand and the prose afterwards.

Design refs: §4.5 (DC ownership: scene fixed DC first, else AI base-band + env modifier),
§4.6 (pipeline → structured result → event_log), §8.3 (intent already structured).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import conditions, dice, guild_rank, rules_5e
from .conditions import CheckOutcome, GateDecision
from .types import (
    Boon,
    BoonMagnitude,
    BoonType,
    Character,
    Cost,
    CostSeverity,
    CostType,
    Intent,
    ResolutionResult,
    ResultBand,
    ResultKind,
    SKILLS,
    TRAINED_RANKS,
    normalize_proficiency_rank,
)
from ..logging_setup import get_logger

if TYPE_CHECKING:
    from ..ai.schemas import DCAssessment
    from ..state.game_state import GameState

log = get_logger("resolution")

# Map common free-text verbs to a governing PF2e skill so the engine is robust even
# when the AI hands us a loose `approach`. Legacy 5e skill words (persuasion, insight,
# investigation, sleight_of_hand…) are kept as synonyms so old data and habits still map.
APPROACH_SYNONYMS: dict[str, str] = {
    "persuade": "diplomacy", "convince": "diplomacy", "talk": "diplomacy", "negotiate": "diplomacy",
    "persuasion": "diplomacy", "command": "diplomacy", "request": "diplomacy",
    "intimidate": "intimidation", "threaten": "intimidation", "menace": "intimidation",
    "deceive": "deception", "lie": "deception", "bluff": "deception", "trick": "deception", "feint": "deception",
    "sneak": "stealth", "hide": "stealth", "skulk": "stealth",
    "climb": "athletics", "jump": "athletics", "swim": "athletics", "force": "athletics",
    "break": "athletics", "shove": "athletics", "grapple": "athletics", "lift": "athletics", "trip": "athletics",
    "lockpick": "thievery", "pick_lock": "thievery", "pick": "thievery", "disable": "thievery",
    "disarm": "thievery", "pickpocket": "thievery", "palm": "thievery", "steal": "thievery",
    "sleight_of_hand": "thievery", "sleight": "thievery",
    "tumble": "acrobatics", "balance": "acrobatics", "flip": "acrobatics",
    "search": "perception", "investigate": "perception", "examine": "perception", "seek": "perception",
    "investigation": "perception", "notice": "perception", "spot": "perception", "listen": "perception",
    "look": "perception", "scan": "perception", "read": "perception", "sense": "perception", "insight": "perception",
    "recall": "arcana", "identify": "arcana", "occult": "occultism",
    "track": "survival", "forage": "survival", "navigate": "survival",
    "heal": "medicine", "first_aid": "medicine", "diagnose": "medicine", "treat": "medicine",
    "perform": "performance", "sing": "performance", "play": "performance",
    "recall_lore": "society", "history": "society", "society": "society", "religion": "religion", "nature": "nature",
    "craft": "crafting", "repair": "crafting", "build": "crafting",
    "tame": "nature", "calm": "nature", "animal_handling": "nature",
}


# Skills that are inherently opposed/risky: even when the player frames the action as
# casual ("I just calmly walk past the guard"), these always resolve against a DC so a
# narrative claim can never become a free success (design §8.3 anti-talk protection).
CONTESTED_SKILLS: frozenset[str] = frozenset({
    "stealth", "deception", "diplomacy", "intimidation", "thievery",
})


def _parametric_loyalty_decision(
    conds: list[str], actor_id: str, approach: str | None,
) -> GateDecision | None:
    """Resolve LOYAL_TO:<ref> / INDEBTED_TO:<ref> against the current actor.

    - loyal_to:<X> + diplomacy/intimidation/deception → AUTO_FAIL unless ref==actor
    - indebted_to:<actor> + diplomacy → AUTO_SUCCESS
    Returns None when no parametric flag fires; the caller falls back to the
    standard catalog gate.
    """
    if approach not in {"diplomacy", "intimidation", "deception"}:
        return None
    for cid in conds:
        base, ref = conditions.parse_parametric(cid)
        if base == conditions.LOYAL_TO and ref and ref != actor_id:
            return GateDecision(
                outcome=CheckOutcome.AUTO_FAIL,
                triggering=(cid,),
                note=f"{conditions.label(cid)}：對效忠對象不利的要求被拒絕",
            )
        if base == conditions.INDEBTED_TO and ref == actor_id and approach == "diplomacy":
            return GateDecision(
                outcome=CheckOutcome.AUTO_SUCCESS,
                triggering=(cid,),
                note=f"{conditions.label(cid)}：因人情自然配合",
            )
    return None


def _disposition_gate(ent: dict | None, approach: str | None) -> GateDecision | None:
    """Turn durable NPC attitude into a pre-roll gate when the fiction is settled.

    `frightened` still means "easier to pressure" and only grants advantage via the
    condition catalog. `cowed` is stronger: the NPC is already broken/submissive, so
    asking for another intimidation roll adds no meaningful uncertainty.
    """
    if not ent:
        return None
    if ent.get("disposition") == "cowed" and approach == "intimidation":
        return GateDecision(
            outcome=CheckOutcome.AUTO_SUCCESS,
            triggering=("disposition:cowed",),
            note="屈服：目標已被嚇到崩潰，再次威嚇無需檢定",
        )
    return None


def gate_for_intent(state: "GameState", intent: Intent) -> GateDecision:
    """Look up the target's mechanical conditions and return a pre-roll gate
    decision (skip / auto-success / auto-fail / adv / disadv).

    Returns ROLL when there is no target, no conditions, or none of them apply
    to this approach/attack category. Called by both `requires_check` (so an
    auto-success bypasses the contested-skill gate) and `resolve` (so the d20
    is actually skipped when warranted).
    """
    if not intent.target:
        return GateDecision(CheckOutcome.ROLL)
    from ..db import store
    ent_id, conds = store.get_conditions_by_ref(state.current_location_id, intent.target)
    ent = store.get_entity_by_id(ent_id) if ent_id else None
    skill = normalize_approach(intent.approach or intent.action)
    # Parametric loyalty / debt is decided against the actor id, so it lives outside
    # the catalog's per-skill table.
    parametric = _parametric_loyalty_decision(conds, intent.actor_id, skill)
    if parametric is not None:
        return parametric
    meta = store.get_meta_by_ref(state.current_location_id, intent.target)
    catalog_gate = conditions.evaluate_gate(
        conds, approach=skill, is_attack=intent.is_attack, condition_meta=meta,
    )
    if catalog_gate.short_circuits:
        return catalog_gate
    disp_gate = _disposition_gate(ent, skill)
    if disp_gate is not None:
        return disp_gate
    return catalog_gate


def requires_check(state: "GameState", intent: Intent) -> bool:
    """Engine gate over the parser's `needs_check` proposal (design §8.3).

    Returns True when the action MUST roll regardless of what the AI proposed — i.e.
    the action is an attack, uses a contested skill, targets a wary/afraid/hostile
    entity, or ENGAGES A PRESENT SCENE OBSTACLE with a skill the scene flags as
    DC-worthy. Trivial, uncontested beats return False and may be narrated without a
    roll.

    Note: a bare skill listed in `scene.challenges` no longer forces a check on its
    own — it only does so when the action actually targets a present scene entity.
    Otherwise a scene-wide `perception` DC (meant for "scan for eavesdroppers") would
    force a roll for reading a map you already hold. `scene.challenges` still owns the
    DC via `determine_dc` once a roll is warranted.

    A target condition that short-circuits the roll (auto-success / auto-fail) also
    returns False here — `resolve` will produce the outcome without a d20 so the
    contested-skill gate cannot force a wasted roll against e.g. a hypnotized NPC.
    """
    if intent.is_attack:
        return True
    gate = gate_for_intent(state, intent)
    if gate.short_circuits:
        return False
    skill = normalize_approach(intent.approach or intent.action)
    if skill in CONTESTED_SKILLS:
        return True
    if intent.target:
        from ..db import store
        ent = store.find_by_ref(state.current_location_id, intent.target)
        if ent:
            # A wary/afraid/hostile/attack target always resists — roll regardless of skill.
            if ent.get("disposition") in ("wary", "afraid", "hostile", "attack"):
                return True
            # Engaging a PRESENT scene obstacle with a skill the author flagged here
            # (e.g. examining the tripwire, searching the wagons) → roll. Reading a
            # held map — whose target is not a present scene entity — stays free.
            if skill in state.scene.challenges and ent.get("status") not in store._ABSENT_STATUSES:
                return True
    return False


def normalize_approach(approach: str | None) -> str:
    """Best-effort map an approach string to a PF2e skill key."""
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
    "thievery": CostType.TRACE,
    "diplomacy": CostType.RELATION,
    "intimidation": CostType.RELATION,
    "performance": CostType.RELATION,
    "athletics": CostType.RESOURCE,
    "acrobatics": CostType.RESOURCE,
    "medicine": CostType.RESOURCE,
    "crafting": CostType.RESOURCE,
    "perception": CostType.TIME,
    "survival": CostType.TIME,
    "arcana": CostType.TIME,
    "occultism": CostType.TIME,
    "society": CostType.TIME,
    "religion": CostType.TIME,
    "nature": CostType.ATTENTION,
}

_DEFAULT_COST: CostType = CostType.TIME


# Action verb (lowercased, stripped) → condition that lands on the target when
# the check SUCCEEDs. Approach is usually `arcana` (魔法系) or the verb itself,
# so we key off `action` first then `approach` second. Kept small for the MVP;
# new spells just add a row here.
_EFFECT_TO_CONDITION: dict[str, str] = {
    # 中文動詞
    "催眠": conditions.HYPNOTIZED,
    "魅惑": conditions.CHARMED,
    "支配": conditions.DOMINATED,
    "嚇": conditions.FRIGHTENED,
    "威嚇": conditions.FRIGHTENED,   # SUCCESS leaves target shaken
    "束縛": conditions.RESTRAINED,
    "纏繞": conditions.RESTRAINED,
    "擒抱": conditions.GRAPPLED,
    "推倒": conditions.PRONE,
    "致盲": conditions.BLINDED,
    "震懾": conditions.STUNNED,
    "麻痺": conditions.PARALYZED,
    # English verbs (parser sometimes returns these)
    "hypnotize": conditions.HYPNOTIZED,
    "hypnotise": conditions.HYPNOTIZED,
    "charm": conditions.CHARMED,
    "dominate": conditions.DOMINATED,
    "frighten": conditions.FRIGHTENED,
    "intimidate": conditions.FRIGHTENED,
    "restrain": conditions.RESTRAINED,
    "entangle": conditions.RESTRAINED,
    "grapple": conditions.GRAPPLED,
    "trip": conditions.PRONE,
    "shove": conditions.PRONE,
    "blind": conditions.BLINDED,
    "stun": conditions.STUNNED,
    "paralyze": conditions.PARALYZED,
}

# Intimidation succeeds → target is shaken, not literally frightened by 5e RAW,
# but the MVP collapses both onto FRIGHTENED so the prompt + gate stay simple.
# Only social verbs that *control or disable* the target produce a condition;
# generic persuasion/insight do not (they should not silence future checks).

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


def _severity_for_band(band: ResultBand) -> CostSeverity:
    """Map a failure band → cost severity (§4.4 + §4.7).

    The degree already encodes the nat-1 fumble (it shifts FAILURE → CRIT_FAILURE), so
    severity keys purely off the band: FAILURE = moderate, CRIT_FAILURE = heavy.
    """
    if band is ResultBand.CRIT_FAILURE:
        return CostSeverity.HEAVY
    return CostSeverity.MODERATE


def pick_cost(state: "GameState", skill: str, band: ResultBand) -> Cost | None:
    """Pick a structured Cost for a FAILURE / CRIT_FAILURE check (§4.7).

    Selection order: scene.cost_pool (if any) → skill default → TIME. Sampling uses
    the dice RNG so the same seed gives the same costs in tests. A CRIT_FAILURE cost is
    heavier (HEAVY) and persists into durable state; an ordinary FAILURE does not.
    """
    if band not in (ResultBand.FAILURE, ResultBand.CRIT_FAILURE):
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

    severity = _severity_for_band(band)
    persistent = band is ResultBand.CRIT_FAILURE
    note = f"{_SEVERITY_NOTE_ZH[severity]}的{_COST_NOTE_ZH[cost_type]}"
    log.info("pick_cost: skill=%s band=%s → type=%s severity=%s persistent=%s",
             skill, band.value, cost_type.value, severity.value, persistent)
    return Cost(type=cost_type, severity=severity, persistent=persistent, note=note)


# Default skill → BoonType fallback for a CRIT_SUCCESS when a scene has no boon_pool
# (§4.4 大成功額外效果). Mirrors _SKILL_DEFAULT_COST — the upside that fits the fiction.
_SKILL_DEFAULT_BOON: dict[str, BoonType] = {
    "stealth": BoonType.OPENING,
    "deception": BoonType.OPENING,
    "thievery": BoonType.RESOURCE_GAIN,
    "diplomacy": BoonType.GOODWILL,
    "intimidation": BoonType.GOODWILL,
    "performance": BoonType.GOODWILL,
    "athletics": BoonType.OPENING,
    "acrobatics": BoonType.OPENING,
    "medicine": BoonType.PROGRESS,
    "crafting": BoonType.PROGRESS,
    "perception": BoonType.EXTRA_INFO,
    "survival": BoonType.TIME_SAVED,
    "arcana": BoonType.EXTRA_INFO,
    "occultism": BoonType.EXTRA_INFO,
    "society": BoonType.EXTRA_INFO,
    "religion": BoonType.EXTRA_INFO,
    "nature": BoonType.EXTRA_INFO,
}

_DEFAULT_BOON: BoonType = BoonType.OPENING

_BOON_NOTE_ZH: dict[BoonType, str] = {
    BoonType.TIME_SAVED: "省下時間",
    BoonType.EXTRA_INFO: "額外情報",
    BoonType.PROGRESS: "進度加成",
    BoonType.RESOURCE_GAIN: "資源回收",
    BoonType.GOODWILL: "關係改善",
    BoonType.OPENING: "創造良機",
}

_BOON_MAGNITUDE_ZH: dict[BoonMagnitude, str] = {
    BoonMagnitude.MINOR: "些許",
    BoonMagnitude.MODERATE: "可觀",
    BoonMagnitude.MAJOR: "顯著",
}


def pick_boon(state: "GameState", skill: str, band: ResultBand) -> Boon | None:
    """Pick a structured Boon for a CRIT_SUCCESS check (§4.4 大成功額外效果).

    The symmetric inverse of pick_cost. Selection order: scene.boon_pool (if any) →
    skill default → OPENING. Sampling uses the shared dice RNG for repeatable tests.
    A crit-success boon is MAJOR and flows into durable state.
    """
    if band is not ResultBand.CRIT_SUCCESS:
        return None

    pool = list(getattr(state.scene, "boon_pool", []) or [])
    boon_type: BoonType | None = None
    if pool:
        try:
            boon_type = BoonType(dice.choice(pool))
        except ValueError:
            log.warning("pick_boon: scene %s has invalid boon_pool entry; falling back", state.scene.id)
            boon_type = None
    if boon_type is None:
        boon_type = _SKILL_DEFAULT_BOON.get(skill, _DEFAULT_BOON)

    magnitude = BoonMagnitude.MAJOR
    note = f"{_BOON_MAGNITUDE_ZH[magnitude]}的{_BOON_NOTE_ZH[boon_type]}"
    log.info("pick_boon: skill=%s band=%s → type=%s magnitude=%s", skill, band.value, boon_type.value, magnitude.value)
    return Boon(type=boon_type, magnitude=magnitude, persistent=True, note=note)


def npc_dc_adjustment(state: "GameState", intent: Intent) -> tuple[int, str | None]:
    """Deterministic DC offset from the target NPC's disposition (design: 態度數值化).

    Applies ONLY to social checks (`conditions.SOCIAL_SKILLS`) aimed at a present-or-known
    entity that carries a disposition — friendlier targets are easier to sway, hostile ones
    resist harder. Returns (offset, disposition); (0, None) when out of scope so callers can
    add it unconditionally. The AI never picks this value; it is looked up from stored state.
    """
    skill = normalize_approach(intent.approach or intent.action)
    if skill not in conditions.SOCIAL_SKILLS or not intent.target:
        return 0, None
    from ..db import store
    ent = store.find_by_ref(state.current_location_id, intent.target)
    if not ent:
        return 0, None
    disp = ent.get("disposition")
    return rules_5e.npc_modifier(disp), disp


def rank_dc_adjustment(state: "GameState", intent: Intent) -> tuple[int, str | None]:
    """Soft rank gate: target/location flags can add pressure without blocking play."""
    actor = state.characters.get(intent.actor_id)
    if actor is None or not intent.target:
        return 0, None
    from ..db import store
    ent = store.find_by_ref(state.current_location_id, intent.target)
    if not ent:
        return 0, None
    flags = ent.get("flags") or {}
    min_rank = flags.get("min_rank")
    gate = str(flags.get("gate") or "soft").lower()
    if not min_rank or gate == "hard":
        return 0, str(min_rank) if min_rank else None
    penalty = guild_rank.under_rank_dc_penalty(str(min_rank), actor)
    return penalty, str(min_rank) if penalty else None


def determine_dc(state: "GameState", intent: Intent, assessment: "DCAssessment | None") -> int:
    """DC ownership: the AI's base-band + env-modifier assessment owns the DC (final DC,
    NOT anchor-snapped). The scene challenge table is only a *fallback* for when the AI
    returned no assessment at all — it is no longer a priority override, and is no longer
    fed to the parser as a reference. A default standard DC backstops both.

    A deterministic NPC disposition offset (design: 態度數值化) is then folded in uniformly
    across all three DC sources and the result re-floored at MIN_DC."""
    if assessment is not None:
        base_dc = assessment.final_dc
        source = "AI assessment"
    else:
        base_dc = None
        challenges = state.scene.challenges or {}
        skill = normalize_approach(intent.approach)
        for key in (skill, intent.approach, intent.action):
            if key and str(key).lower() in challenges:
                base_dc = int(challenges[str(key).lower()])
                source = f"scene challenge {key!r}"
                break
        if base_dc is None:
            base_dc = rules_5e.BAND_DC["normal"]
            source = "default normal"
    npc_mod, disp = npc_dc_adjustment(state, intent)
    rank_mod, min_rank = rank_dc_adjustment(state, intent)
    final = max(rules_5e.MIN_DC, base_dc + npc_mod + rank_mod)
    log.debug("determine_dc: %s base=%d npc=%+d (%s) rank=%+d (%s) → DC %d",
              source, base_dc, npc_mod, disp or "—", rank_mod, min_rank or "—", final)
    return final


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
            names = "、".join(
                h.name for h in helper_chars
                if normalize_proficiency_rank(h.skill_prof.get(skill)) in TRAINED_RANKS
            )
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


def _condition_for(action: str | None, approach: str | None) -> str | None:
    """Look up the condition that should land on the target when this check
    succeeds. Returns None when nothing maps."""
    for key in (action, approach):
        if not key:
            continue
        k = str(key).strip().lower()
        if k in _EFFECT_TO_CONDITION:
            return _EFFECT_TO_CONDITION[k]
    return None


def _apply_condition_effects(
    state: "GameState", intent: Intent, result: ResolutionResult,
) -> None:
    """If the check produced a success degree (CRIT_SUCCESS/SUCCESS) on an action that
    controls or disables the target (catalog lookup), attach the matching condition to
    the target entity. FAILURE / CRIT_FAILURE do not."""
    if result.band not in (ResultBand.CRIT_SUCCESS, ResultBand.SUCCESS):
        return
    if not intent.target:
        return
    cid = _condition_for(intent.action, intent.approach)
    if cid is None:
        return
    from ..db import store
    ent = store.find_by_ref(state.current_location_id, intent.target)
    if ent is None:
        return
    if store.add_condition(ent["id"], cid):
        log.info("resolve: auto-applied condition %s to entity %s (action=%s approach=%s)",
                 cid, ent["id"], intent.action, intent.approach)
        result.deltas.append(f"狀態：{conditions.label(cid)}")


def _build_short_circuit_result(
    actor: Character,
    intent: Intent,
    skill: str,
    dc: int,
    gate: GateDecision,
) -> ResolutionResult:
    """Construct a ResolutionResult when a target condition skips the d20.

    AUTO_SUCCESS → SUCCESS band, no cost. AUTO_FAIL → FAILURE band, no cost
    (the gate already explains why; layering a structured cost on top would
    double-narrate it). AUTO_CRIT is treated as SUCCESS here in the out-of-
    combat path; combat attacks handle the crit separately.
    """
    if gate.outcome is CheckOutcome.AUTO_FAIL:
        band = ResultBand.FAILURE
        success = False
        verdict = "FAILURE"
        hint = "Describe a setback; the target's condition makes the action moot."
    else:
        band = ResultBand.SUCCESS
        success = True
        verdict = "SUCCESS"
        hint = "Describe a clean success that follows from the target's condition."

    label = skill.replace("_", " ").title() if skill else "Check"
    summary = f"{label} check vs DC {dc}: {verdict} (條件短路)"
    result = ResolutionResult(
        kind=ResultKind.CHECK,
        actor_id=actor.id,
        actor_name=actor.name,
        success=success,
        band=band,
        dc=dc,
        roll_breakdown="無需擲骰（目標狀態觸發短路）",
        summary=summary,
        narration_hint=hint,
    )
    if intent.target:
        result.target_name = intent.target
    if intent.raw_text:
        result.raw_text = intent.raw_text
    if intent.topic:
        result.topic = intent.topic
    if gate.note:
        result.deltas.append(f"條件觸發：{gate.note}")
    return result


def _attach_dc_audit(
    result: ResolutionResult, assessment: "DCAssessment | None", dc: int,
    state: "GameState", intent: Intent,
) -> None:
    """Record the DC breakdown on the result. The deterministic NPC disposition offset
    (design: 態度數值化) is always recorded when in scope. The AI base/env breakdown is
    recorded when the AI assessment was the effective DC source — i.e. `dc` equals the
    assessment's final DC plus the npc offset. Skipped when a scene fixed DC overrode it
    (GM-set DCs have no base+env decomposition); `result.dc` carries the final value
    either way."""
    npc_mod, disp = npc_dc_adjustment(state, intent)
    if disp is not None:
        result.dc_npc_modifier = npc_mod
        result.dc_npc_disposition = disp
    if assessment is not None and dc == assessment.final_dc + npc_mod:
        result.dc_base = assessment.base_dc
        result.dc_env_modifier = assessment.env_modifier
        result.dc_env_reason = assessment.env_reason


def _apply_degree_drop(result: ResolutionResult, note: str, *, target_side: bool = False) -> None:
    """Drop the result one degree of success (PF2e band_downgrade, e.g. under_duress).

    Recomputes the back-compat `success` flag and rewrites the verdict word in the
    summary via the canonical band labels (instead of a brittle literal replace). No-op
    when already at the bottom of the ladder.
    """
    old = result.band
    if old is None:
        return
    new = rules_5e.shift_band(old, -1)
    if new is old:
        return
    result.band = new
    result.success = rules_5e.success_for(new)
    result.summary = result.summary.replace(
        rules_5e._BAND_LABEL[old], rules_5e._BAND_LABEL[new], 1
    )
    prefix = "目標狀態降級" if target_side else "狀態降級"
    result.deltas.append(f"{prefix}：{note}")


def resolve(
    state: "GameState",
    intent: Intent,
    *,
    assessment: "DCAssessment | None" = None,
    helpers: list[str] | None = None,
    env_tier: int = 0,
    tool_bonus: int = 0,
    resource_spend: bool = False,
) -> ResolutionResult:
    """Resolve an out-of-combat Tier-A intent into a ResolutionResult and log it.

    `assessment` is the AI's DC breakdown (base band + env modifier); the engine owns
    the final DC via determine_dc (scene fixed DC still wins). `helpers`, `env_tier`,
    `tool_bonus`, `resource_spend` plug into §4.9 external bonuses on the ROLL side —
    never the DC. The engine combines and caps them; AI never picks any of these numbers.
    """
    actor = state.characters.get(intent.actor_id)
    if actor is None:
        log.error("resolve: unknown actor=%s known=%s", intent.actor_id, list(state.characters))
        raise KeyError(f"Unknown actor: {intent.actor_id}")

    skill = normalize_approach(intent.approach or intent.action)
    dc = determine_dc(state, intent, assessment)
    advantage, disadvantage = state.scene.advantage_for(skill)

    # Pre-roll condition gate (target charmed/hypnotized/dead/already_resolved …).
    # Short-circuit outcomes bypass the d20 entirely; adv/disadv stack with the
    # scene's own modifiers and cancel as in 5e.
    gate = gate_for_intent(state, intent)
    if gate.advantage:
        advantage = True
    if gate.disadvantage:
        disadvantage = True

    # Actor's own conditions (bless / exhaustion / poisoned / under_duress / …).
    # We pass condition_meta lazily — Character.conditions is a flat list[str] today,
    # so EXHAUSTED defaults to L1 unless meta is wired later via condition_meta.
    actor_meta = getattr(actor, "condition_meta", None) or {}
    actor_effect = conditions.evaluate_actor(
        actor.conditions, approach=skill, is_attack=intent.is_attack,
        condition_meta=actor_meta,
    )
    if actor_effect.advantage:
        advantage = True
    if actor_effect.disadvantage:
        disadvantage = True
    if advantage and disadvantage:
        advantage = disadvantage = False

    if gate.short_circuits:
        log.info("resolve: condition gate short-circuit outcome=%s triggering=%s",
                 gate.outcome.value, ",".join(gate.triggering))
        result = _build_short_circuit_result(actor, intent, skill, dc, gate)
        _attach_dc_audit(result, assessment, dc, state, intent)
        state.log_result(result)
        return result

    if actor_effect.short_circuits:
        log.info("resolve: actor effect short-circuit outcome=%s triggering=%s",
                 actor_effect.outcome.value, ",".join(actor_effect.triggering))
        result = _build_short_circuit_result(
            actor, intent, skill, dc,
            GateDecision(outcome=actor_effect.outcome,
                         triggering=actor_effect.triggering,
                         note=actor_effect.note),
        )
        _attach_dc_audit(result, assessment, dc, state, intent)
        state.log_result(result)
        return result

    external_bonus, external_parts = _compose_external(
        state, actor, skill, helpers, env_tier, tool_bonus, resource_spend
    )

    # Actor's +1d4 / -1d4 (bless / guidance / bane). Roll once and fold into the
    # external bonus so it shows up in the dice breakdown alongside assist etc.
    if actor_effect.bonus_dice:
        sign = +1 if actor_effect.bonus_dice > 0 else -1
        die = dice.roll_dice(1, abs(actor_effect.bonus_dice), 0).total
        external_bonus += sign * die
        label_zh = "+" if sign > 0 else "−"
        external_parts.append(f"{label_zh}1d{abs(actor_effect.bonus_dice)}({sign*die:+d}：{actor_effect.note.split('：')[0]})")

    log.info("resolve: actor=%s skill=%s dc=%s adv=%s disadv=%s external=%+d (%s)",
             actor.name, skill, dc, advantage, disadvantage, external_bonus,
             ", ".join(external_parts) or "none")

    result = rules_5e.ability_check(
        actor, skill, dc,
        advantage=advantage,
        disadvantage=disadvantage,
        external_bonus=external_bonus,
    )
    _attach_dc_audit(result, assessment, dc, state, intent)
    if intent.target:
        result.target_name = intent.target
    # Pass the player's original utterance through so the narrator sees who they
    # actually addressed — otherwise it has to guess from the scene description.
    if intent.raw_text:
        result.raw_text = intent.raw_text
    # Topic (e.g. "內褲顏色") goes through so the narrator stays literal instead
    # of collapsing to "they ask a question".
    if intent.topic:
        result.topic = intent.topic

    if external_parts:
        result.deltas.append("外部加值：" + "、".join(external_parts))
    rank_mod, min_rank = rank_dc_adjustment(state, intent)
    if rank_mod:
        result.deltas.append(f"階級壓力：需要 {min_rank} 級，DC +{rank_mod}")

    # Actor-side degree drop (e.g. under_duress on the actor itself — rare for PCs, but
    # the mechanism is symmetric). Drops one degree of success (PF2e): CRIT_SUCCESS→SUCCESS,
    # SUCCESS→FAILURE, so a coerced success no longer comes clean.
    if actor_effect.band_downgrade and result.band in (ResultBand.CRIT_SUCCESS, ResultBand.SUCCESS):
        _apply_degree_drop(result, actor_effect.note)

    # Target-side D-flag: a coerced NPC who's the *target* of social pressure also drops a
    # degree, so the goal lands but not cleanly.
    if intent.target and result.band in (ResultBand.CRIT_SUCCESS, ResultBand.SUCCESS):
        from ..db import store as _store
        _, target_conds = _store.get_conditions_by_ref(state.current_location_id, intent.target)
        for cid in target_conds:
            base, _ref = conditions.parse_parametric(cid)
            eff = conditions.CATALOG.get(base)
            if eff and eff.band_downgrade:
                _apply_degree_drop(result, eff.label_zh, target_side=True)
                break

    # Structured cost on FAILURE / CRIT_FAILURE, or a boon on CRIT_SUCCESS (§4.4/§4.7).
    # Recorded into deltas so the dashboard log + future RAG/history layer can read it as
    # plain text without re-parsing JSON.
    if result.band in (ResultBand.FAILURE, ResultBand.CRIT_FAILURE):
        cost = pick_cost(state, skill, result.band)
        if cost is not None:
            result.cost = cost
            result.deltas.append(f"代價：{cost.note}")
    elif result.band is ResultBand.CRIT_SUCCESS:
        boon = pick_boon(state, skill, result.band)
        if boon is not None:
            result.boon = boon
            result.deltas.append(f"增益：{boon.note}")

    # Auto-attach conditions for spells/effects that control or disable a target
    # (e.g. 催眠術 SUCCESS → hypnotized on the target entity). Done before logging
    # so the dashboard row shows the state change in its deltas.
    _apply_condition_effects(state, intent, result)

    log.info("resolve: result band=%s success=%s roll=%s summary=%s target=%s cost=%s",
             result.band.value if result.band else None,
             result.success, result.roll_breakdown, result.summary, result.target_name,
             result.cost.type.value if result.cost else None)
    state.log_result(result)
    return result


def narrative_beat(
    state: "GameState",
    actor: Character,
    summary: str,
    *,
    target_name: str | None = None,
    raw_text: str = "",
    hint: str = "",
) -> ResolutionResult:
    """A no-roll beat for a trivial/uncontested Tier-A action (design §8.2). Still
    produces a logged result so the dashboard, event log, and RAG layer see it — there
    is just no d20 and no success/failure band. `target_name`/`raw_text` are set before
    logging so the narrator (and history) know what the actor addressed."""
    result = ResolutionResult(
        kind=ResultKind.NARRATIVE,
        actor_id=actor.id,
        actor_name=actor.name,
        summary=summary,
        target_name=target_name,
        raw_text=raw_text,
        narration_hint=hint or "Describe the moment vividly but briefly.",
    )
    state.log_result(result)
    return result
