"""Resolution pipeline — turns a structured Intent into a ResolutionResult.

This is the out-of-combat path (skill/ability checks and narrative beats). Combat
actions are routed through combat.py. Either way the engine owns every number;
the AI only supplies the Intent slots beforehand and the prose afterwards.

Design refs: §4.5 (DC ownership: scene table first, AI proposal snapped to anchors),
§4.6 (pipeline → structured result → event_log), §8.3 (intent already structured).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from . import conditions, dice, rules_5e
from .conditions import CheckOutcome, GateDecision
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


# Skills that are inherently opposed/risky: even when the player frames the action as
# casual ("I just calmly walk past the guard"), these always resolve against a DC so a
# narrative claim can never become a free success (design §8.3 anti-talk protection).
CONTESTED_SKILLS: frozenset[str] = frozenset({
    "stealth", "deception", "persuasion", "intimidation", "sleight_of_hand",
})


def _parametric_loyalty_decision(
    conds: list[str], actor_id: str, approach: str | None,
) -> GateDecision | None:
    """Resolve LOYAL_TO:<ref> / INDEBTED_TO:<ref> against the current actor.

    - loyal_to:<X> + persuasion/intimidation/deception → AUTO_FAIL unless ref==actor
    - indebted_to:<actor> + persuasion → AUTO_SUCCESS
    Returns None when no parametric flag fires; the caller falls back to the
    standard catalog gate.
    """
    if approach not in {"persuasion", "intimidation", "deception"}:
        return None
    for cid in conds:
        base, ref = conditions.parse_parametric(cid)
        if base == conditions.LOYAL_TO and ref and ref != actor_id:
            return GateDecision(
                outcome=CheckOutcome.AUTO_FAIL,
                triggering=(cid,),
                note=f"{conditions.label(cid)}：對效忠對象不利的要求被拒絕",
            )
        if base == conditions.INDEBTED_TO and ref == actor_id and approach == "persuasion":
            return GateDecision(
                outcome=CheckOutcome.AUTO_SUCCESS,
                triggering=(cid,),
                note=f"{conditions.label(cid)}：因人情自然配合",
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
    _, conds = store.get_conditions_by_ref(state.current_location_id, intent.target)
    if not conds:
        return GateDecision(CheckOutcome.ROLL)
    skill = normalize_approach(intent.approach or intent.action)
    # Parametric loyalty / debt is decided against the actor id, so it lives outside
    # the catalog's per-skill table.
    parametric = _parametric_loyalty_decision(conds, intent.actor_id, skill)
    if parametric is not None:
        return parametric
    meta = store.get_meta_by_ref(state.current_location_id, intent.target)
    return conditions.evaluate_gate(
        conds, approach=skill, is_attack=intent.is_attack, condition_meta=meta,
    )


def requires_check(state: "GameState", intent: Intent) -> bool:
    """Engine gate over the parser's `needs_check` proposal (design §8.3).

    Returns True when the action MUST roll regardless of what the AI proposed — i.e.
    the action is an attack, uses a contested skill, hits a scene-authored challenge,
    or targets a wary/afraid/hostile entity. Trivial, uncontested beats return False
    and may be narrated without a roll.

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
    if skill in state.scene.challenges:        # the scene author flagged a DC here
        return True
    if intent.target:
        from ..db import store
        ent = store.find_by_ref(state.current_location_id, intent.target)
        if ent and ent.get("disposition") in ("wary", "afraid", "hostile"):
            return True
    return False


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
    "威嚇": conditions.FRIGHTENED,   # PARTIAL/SUCCESS leaves target shaken
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
    """If the check produced SUCCESS/PARTIAL on an action that controls or
    disables the target (catalog lookup), attach the matching condition to the
    target entity. PARTIAL still applies — the goal was achieved — but FAILURE
    does not."""
    if result.band not in (ResultBand.SUCCESS, ResultBand.PARTIAL):
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

    # Actor-side band downgrade (e.g. under_duress on the actor itself — rare for
    # PCs, but the mechanism is symmetric). SUCCESS becomes PARTIAL so the prose
    # carries a cost the player can feel.
    if actor_effect.band_downgrade and result.band is ResultBand.SUCCESS:
        result.band = ResultBand.PARTIAL
        result.summary = result.summary.replace("SUCCESS", "PARTIAL")
        result.deltas.append(f"狀態降級：{actor_effect.note}")

    # Target-side D-flag: a coerced NPC who's the *target* of social pressure shouldn't
    # give a clean SUCCESS either — the goal IS achieved but the cost lands.
    if intent.target and result.band is ResultBand.SUCCESS:
        from ..db import store as _store
        _, target_conds = _store.get_conditions_by_ref(state.current_location_id, intent.target)
        for cid in target_conds:
            base, _ref = conditions.parse_parametric(cid)
            eff = conditions.CATALOG.get(base)
            if eff and eff.band_downgrade:
                result.band = ResultBand.PARTIAL
                result.summary = result.summary.replace("SUCCESS", "PARTIAL")
                result.deltas.append(f"目標狀態降級：{eff.label_zh}")
                break

    # Structured cost on PARTIAL/FAILURE (§4.7). Recorded into deltas so the dashboard
    # log + future RAG/history layer can read it as plain text without re-parsing JSON.
    if result.band and result.band is not ResultBand.SUCCESS:
        cost = pick_cost(state, skill, result.band, fumble=result.fumble)
        if cost is not None:
            result.cost = cost
            result.deltas.append(f"代價：{cost.note}")

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
