"""Standard D&D 5e resolution math.

Every function here is pure program logic that may roll dice (via dice.py) and mutate
Character state, but never calls the AI. This is the 'truth source' of the MVP.
"""
from __future__ import annotations

from . import dice
from .types import (
    Ability,
    Action,
    ActionType,
    Character,
    Damage,
    ResolutionResult,
    ResultBand,
    ResultKind,
    SKILLS,
    TRAINED_RANKS,
    normalize_proficiency_rank,
)

# Difficulty ladder (design §4.3 — four-tier, standard = DC 10). The ladder supplies the
# *base* DC for a check: the intent parser judges how hard the player's chosen *method* is
# and picks one band; the engine then adds an AI-judged environment modifier (see
# dc_from_band). The final DC is intentionally NOT snapped back to these anchors.
DC_LABELS: dict[int, str] = {
    5: "容易",
    10: "標準",
    15: "困難",
    20: "極難",
}
DC_ANCHORS: list[int] = sorted(DC_LABELS)

# Named difficulty bands → base DC. The intent parser picks a band for the action's
# *method* (using the right tool/skill → low band; brute force → high band). `normal` is
# the standard/中間 difficulty, deliberately anchored at DC 10.
BAND_DC: dict[str, int] = {
    "easy": 5,
    "normal": 10,
    "hard": 15,
    "extreme": 20,
}

# Environment modifier (design: 場景目標難度). AI judges the current scene/target and
# returns a signed offset in ±ENV_MODIFIER_CAP (favourable → negative, hostile → positive).
ENV_MODIFIER_CAP: int = 4

# NPC disposition → DC offset (design: 態度數值化). A deterministic, engine-owned offset
# applied to SOCIAL checks against a dispositioned NPC: a friendlier target is easier to
# sway, a hostile one resists harder. The AI never picks this — it is looked up from the
# target entity's stored `disposition` by resolution.npc_dc_adjustment.
DISPOSITION_DC_MOD: dict[str, int] = {
    "friendly": -3,
    "afraid": -1,
    "cowed": -2,
    "neutral": 0,
    "wary": +1,
    "hostile": +3,
    "attack": +5,
}


def npc_modifier(disposition: str | None) -> int:
    """DC offset for a target NPC's disposition; 0 for unknown/None (design: 態度數值化)."""
    return DISPOSITION_DC_MOD.get(disposition or "", 0)
# Floor for any computed DC. A right-tool-on-an-easy-target combo can drop below 5
# (e.g. base 5 − 3 = DC 2 ≈ "almost certainly succeeds"); MIN_DC just stops it going
# nonsensical (≤0 would auto-pass even a nat-1 fumble before mods).
MIN_DC: int = 1


# Four-degree margin threshold (design §4.4 — symmetric ±10, PF2e degrees of success).
# - CRIT_SUCCESS: total ≥ DC + CRIT_MARGIN
# - SUCCESS:      total ≥ DC
# - FAILURE:      total < DC
# - CRIT_FAILURE: total ≤ DC - CRIT_MARGIN
# A natural 20 shifts the degree up one step, a natural 1 down one step.
CRIT_MARGIN: int = 10

# Helper & external-bonus caps (design §4.9). Per-helper schedule is diminishing so
# extras quickly stop counting; everything together is bounded by EXTERNAL_BONUS_CAP
# (≈ two DC tiers) so §4.2 bounded accuracy and §4.3 DC anchors stay meaningful.
ASSIST_BONUSES: tuple[int, ...] = (2, 1)   # 1st helper +2, 2nd +1, rest +0
ASSIST_CAP: int = 3
EXTERNAL_BONUS_CAP: int = 10                # two anchor steps of 5
# Flat bonus granted by §4.9 "消耗資源換加值" (one DC tier worth, folded into the cap).
RESOURCE_SPEND_BONUS: int = 5


def assist_bonus(helpers: list[Character], skill: str) -> int:
    """§4.9 協力遞減封頂: helpers must be proficient in `skill` to count at all.

    Proficient helpers contribute in order: ASSIST_BONUSES[0], [1], 0, 0, …
    Total is then floored at ASSIST_CAP. Non-proficient helpers contribute nothing
    (design: 外行幫不上忙) — so a bard who can't pick locks doesn't add to a stealth
    or thievery check via "I help".
    """
    if skill not in SKILLS:
        # Raw ability or unknown approach: proficiency check doesn't apply, so we
        # can't tell who's qualified. Be strict — return 0 rather than over-granting.
        return 0
    qualifying = [
        h for h in helpers
        if normalize_proficiency_rank(h.skill_prof.get(skill)) in TRAINED_RANKS
    ]
    bonus = 0
    for i, _ in enumerate(qualifying):
        bonus += ASSIST_BONUSES[i] if i < len(ASSIST_BONUSES) else 0
    return min(bonus, ASSIST_CAP)


def cap_external(raw: int) -> int:
    """Clamp a signed combined external bonus to ±EXTERNAL_BONUS_CAP (§4.9)."""
    return max(-EXTERNAL_BONUS_CAP, min(EXTERNAL_BONUS_CAP, raw))


# Degree-of-success ladder, lowest → highest. Public so resolution can apply a one-degree
# downgrade (band_downgrade conditions) through the same single source of truth.
_BAND_LADDER: list[ResultBand] = [
    ResultBand.CRIT_FAILURE,
    ResultBand.FAILURE,
    ResultBand.SUCCESS,
    ResultBand.CRIT_SUCCESS,
]


def shift_band(band: ResultBand, steps: int) -> ResultBand:
    """Shift a band along the four-degree ladder; capped at the ends (§4.4)."""
    i = _BAND_LADDER.index(band)
    i = max(0, min(len(_BAND_LADDER) - 1, i + steps))
    return _BAND_LADDER[i]


def classify_band(total: int, dc: int, *, nat: int | None = None) -> ResultBand:
    """Map (total, DC) plus an optional natural d20 to a ResultBand.

    Pure function, no dice. Symmetric ±CRIT_MARGIN four-degree (§4.4):
    crit success at ≥ DC+10, crit failure at ≤ DC-10. Nat 20 / nat 1 then shift the
    degree one notch up / down.
    """
    if total >= dc + CRIT_MARGIN:
        band = ResultBand.CRIT_SUCCESS
    elif total >= dc:
        band = ResultBand.SUCCESS
    elif total <= dc - CRIT_MARGIN:
        band = ResultBand.CRIT_FAILURE
    else:
        band = ResultBand.FAILURE
    if nat == 20:
        band = shift_band(band, +1)
    elif nat == 1:
        band = shift_band(band, -1)
    return band


def nearest_anchor(dc: int) -> int:
    """Snap an arbitrary DC to the nearest ladder anchor. Retained as a utility; the DC
    flow no longer snaps final DCs (design: 最終 DC = base + 環境修正, 不吸附)."""
    return min(DC_ANCHORS, key=lambda a: abs(a - dc))


def dc_from_band(band: str, env_modifier: int = 0) -> tuple[int, int, int]:
    """Compose a final DC from a difficulty band plus an environment modifier (§4.3).

    `band` selects the base DC (action-method difficulty). `env_modifier` is the
    scene/target difficulty offset, clamped to ±ENV_MODIFIER_CAP. The final DC is
    floored at MIN_DC and deliberately NOT snapped to an anchor.

    Returns (final_dc, base_dc, env_modifier_clamped) so callers can store the full
    breakdown for auditing/debugging.
    """
    base = BAND_DC.get(band, BAND_DC["normal"])
    env = max(-ENV_MODIFIER_CAP, min(ENV_MODIFIER_CAP, int(env_modifier)))
    final = max(MIN_DC, base + env)
    return final, base, env


# ───────────────────────── Ability / skill checks ─────────────────────────
def check_bonus(actor: Character, key: str) -> tuple[int, str]:
    """Resolve a skill name or ability into (bonus, label)."""
    k = key.lower()
    if k in SKILLS:
        return actor.skill_bonus(k), k.replace("_", " ").title()
    try:
        ab = Ability(key.upper())
        return actor.mod(ab), f"{ab.value} check"
    except ValueError:
        # Unknown approach — treat as a raw ability-less check (flat d20).
        return 0, key


_BAND_LABEL: dict[ResultBand, str] = {
    ResultBand.CRIT_SUCCESS: "CRIT SUCCESS",
    ResultBand.SUCCESS: "SUCCESS",
    ResultBand.FAILURE: "FAILURE",
    ResultBand.CRIT_FAILURE: "CRIT FAILURE",
}

_BAND_HINT: dict[ResultBand, str] = {
    ResultBand.CRIT_SUCCESS: "A decisive, exceptional success — the goal is achieved and an extra boon lands; weave it in.",
    ResultBand.SUCCESS: "Describe a clean success.",
    ResultBand.FAILURE: "Describe a setback; the attempt fails and a cost lands.",
    ResultBand.CRIT_FAILURE: "A disastrous fumble — the attempt fails badly and a heavy cost lands.",
}


def band_verdict(band: ResultBand) -> str:
    """Mechanical verdict word for a band, used in result summaries."""
    return _BAND_LABEL[band]


def success_for(band: ResultBand) -> bool:
    """The back-compat boolean view: a success degree (crit-success or success)."""
    return band in (ResultBand.CRIT_SUCCESS, ResultBand.SUCCESS)


def ability_check(
    actor: Character,
    key: str,
    dc: int,
    *,
    advantage: bool = False,
    disadvantage: bool = False,
    external_bonus: int = 0,
) -> ResolutionResult:
    """Resolve a skill/ability check vs a DC using the four-degree scheme (§4.4).

    Returns a ResolutionResult with both `band` (CRIT_SUCCESS/SUCCESS/FAILURE/CRIT_FAILURE)
    and the back-compat `success` boolean: a success degree (crit-success or success) →
    True. Structured cost (FAILURE/CRIT_FAILURE) and boon (CRIT_SUCCESS) are attached
    upstream in resolution.resolve.

    `external_bonus` (design §4.9) folds assist + environment + tool + resource into
    a single signed offset; callers are expected to have already capped it via
    cap_external. It is added on top of the actor's own skill/ability modifier and
    surfaced in `roll_breakdown` so the embed reveals where the help came from.
    """
    bonus, label = check_bonus(actor, key)
    roll = dice.roll_d20(bonus + external_bonus, advantage=advantage, disadvantage=disadvantage)
    band = classify_band(roll.total, dc, nat=roll.natural)
    success = success_for(band)

    breakdown = roll.breakdown()
    if external_bonus:
        sign = "+" if external_bonus > 0 else ""
        breakdown += f"  [含外部 {sign}{external_bonus}]"

    verdict = _BAND_LABEL[band]
    flavour = " (crit!)" if roll.is_crit else " (fumble!)" if roll.is_fumble else ""
    hint = _BAND_HINT[band]
    return ResolutionResult(
        kind=ResultKind.CHECK,
        actor_id=actor.id,
        actor_name=actor.name,
        success=success,
        band=band,
        dc=dc,
        roll_breakdown=breakdown,
        natural=roll.natural,
        crit=roll.is_crit,
        fumble=roll.is_fumble,
        summary=f"{label} check vs DC {dc}: {verdict}{flavour}",
        narration_hint=hint,
    )


def opposed_check(
    actor: Character,
    defender: Character,
    approach: str,
    defense: str,
    *,
    advantage: bool = False,
    disadvantage: bool = False,
    external_bonus: int = 0,
) -> ResolutionResult:
    """§4.10 對抗檢定: passive defender becomes a static DC = 10 + their mod on `defense`.

    Only the actor rolls — async-friendly (a stealth attempt vs an offline defender
    still resolves). Goes through the same four-degree/cost pipeline as ability_check.
    `approach` is the actor's skill/ability; `defense` is the defender's resisting
    skill or ability (e.g. stealth vs perception, athletics vs athletics for grapple).
    """
    def_bonus, def_label = check_bonus(defender, defense)
    dc = 10 + def_bonus
    res = ability_check(
        actor,
        approach,
        dc,
        advantage=advantage,
        disadvantage=disadvantage,
        external_bonus=external_bonus,
    )
    res.target_id = defender.id
    res.target_name = defender.name
    # Make the opposed nature visible in the mechanical summary so the dashboard log
    # and narrator both see "vs <defender>'s <defense>" instead of a bare DC.
    res.summary = f"{res.summary} (vs {defender.name}'s {def_label})"
    return res


# ───────────────────────── Damage / hp ─────────────────────────
def apply_damage(target: Character, amount: int) -> list[str]:
    """Subtract hp; handle downing/death. Returns plain-language deltas."""
    amount = max(0, amount)
    before = target.hp
    target.hp = max(0, target.hp - amount)
    deltas = [f"{target.name} takes {amount} damage ({before}→{target.hp} HP)"]

    if target.hp == 0 and before > 0:
        if target.is_pc:
            if "unconscious" not in target.conditions:
                target.conditions.append("unconscious")
            target.death_successes = 0
            target.death_failures = 0
            # 5e massive-damage instant death: overflow >= max HP.
            if amount - before >= target.max_hp:
                _mark_dead(target)
                deltas.append(f"{target.name} suffers massive damage and dies outright!")
            else:
                deltas.append(f"{target.name} drops to 0 HP and falls unconscious (death saves begin)")
        else:
            _mark_dead(target)
            deltas.append(f"{target.name} is defeated")
    return deltas


def apply_healing(target: Character, amount: int) -> list[str]:
    amount = max(0, amount)
    if "dead" in target.conditions:
        return [f"{target.name} is dead and cannot be healed by this"]
    before = target.hp
    target.hp = min(target.max_hp, target.hp + amount)
    deltas = [f"{target.name} regains {target.hp - before} HP ({before}→{target.hp})"]
    if before == 0 and target.hp > 0:
        for c in ("unconscious",):
            if c in target.conditions:
                target.conditions.remove(c)
        target.death_successes = 0
        target.death_failures = 0
        deltas.append(f"{target.name} is back on their feet")
    return deltas


def _mark_dead(target: Character) -> None:
    for c in ("unconscious",):
        if c in target.conditions:
            target.conditions.remove(c)
    if "dead" not in target.conditions:
        target.conditions.append("dead")


# ───────────────────────── Attacks ─────────────────────────
def attack(
    attacker: Character,
    defender: Character,
    action: Action,
    *,
    advantage: bool = False,
    disadvantage: bool = False,
) -> ResolutionResult:
    """Resolve a weapon/spell attack roll vs AC, then damage. 5e crit = nat 20 doubles dice."""
    roll = dice.roll_d20(action.to_hit, advantage=advantage, disadvantage=disadvantage)
    hit = (roll.total >= defender.ac or roll.is_crit) and not roll.is_fumble

    deltas: list[str] = []
    total_damage = 0
    if hit:
        for dmg in action.damage:
            dr = dice.roll_dice(dmg.count, dmg.sides, dmg.bonus, crit=roll.is_crit)
            total_damage += dr.total
        deltas = apply_damage(defender, total_damage)

    verdict = "HIT" if hit else "MISS"
    flavour = " (critical!)" if roll.is_crit and hit else " (fumble!)" if roll.is_fumble else ""
    return ResolutionResult(
        kind=ResultKind.ATTACK,
        actor_id=attacker.id,
        actor_name=attacker.name,
        target_id=defender.id,
        target_name=defender.name,
        success=hit,
        dc=defender.ac,
        roll_breakdown=f"{action.name}: {roll.breakdown()} vs AC {defender.ac}",
        natural=roll.natural,
        crit=roll.is_crit and hit,
        fumble=roll.is_fumble,
        damage=total_damage if hit else 0,
        deltas=deltas,
        summary=f"{attacker.name} attacks {defender.name} with {action.name}: {verdict}{flavour}"
        + (f" for {total_damage} damage" if hit else ""),
        narration_hint="Land a solid, cinematic blow." if hit else "The attack misses or is turned aside.",
    )


def saving_throw(
    target: Character,
    ability: Ability,
    dc: int,
    *,
    advantage: bool = False,
    disadvantage: bool = False,
) -> tuple[bool, str]:
    """Roll a saving throw. Returns (success, breakdown)."""
    bonus = target.save_bonus(ability)
    roll = dice.roll_d20(bonus, advantage=advantage, disadvantage=disadvantage)
    success = roll.total >= dc
    if roll.is_crit:
        success = True
    elif roll.is_fumble:
        success = False
    return success, f"{ability.value} save: {roll.breakdown()} vs DC {dc}"


def save_spell(caster: Character, target: Character, action: Action) -> ResolutionResult:
    """Resolve a save-for-damage spell (e.g. Sacred Flame, Burning Hands)."""
    assert action.save_ability is not None and action.save_dc is not None
    saved, sb = saving_throw(target, action.save_ability, action.save_dc)

    total = 0
    for dmg in action.damage:
        dr = dice.roll_dice(dmg.count, dmg.sides, dmg.bonus)
        total += dr.total
    if saved and action.save_effect == "half":
        total //= 2
    elif saved and action.save_effect == "none":
        total = 0

    deltas = apply_damage(target, total) if total else [f"{target.name} is unharmed"]
    verdict = "SAVED" if saved else "FAILED save"
    return ResolutionResult(
        kind=ResultKind.SAVE,
        actor_id=caster.id,
        actor_name=caster.name,
        target_id=target.id,
        target_name=target.name,
        success=not saved,            # success = caster's spell landed effectively
        dc=action.save_dc,
        roll_breakdown=f"{action.name}: {sb}",
        damage=total,
        deltas=deltas,
        summary=f"{caster.name} casts {action.name} on {target.name}: {target.name} {verdict}"
        + (f", takes {total} damage" if total else ""),
        narration_hint="Magic flares against the target." if not saved else "The target resists the worst of it.",
    )


def auto_hit(caster: Character, target: Character, action: Action) -> ResolutionResult:
    """Resolve an auto-hit effect (e.g. Magic Missile)."""
    total = 0
    for dmg in action.damage:
        dr = dice.roll_dice(dmg.count, dmg.sides, dmg.bonus)
        total += dr.total
    deltas = apply_damage(target, total)
    return ResolutionResult(
        kind=ResultKind.ATTACK,
        actor_id=caster.id,
        actor_name=caster.name,
        target_id=target.id,
        target_name=target.name,
        success=True,
        damage=total,
        roll_breakdown=f"{action.name}: auto-hit for {total}",
        deltas=deltas,
        summary=f"{caster.name} casts {action.name} on {target.name}: {total} damage (auto-hit)",
        narration_hint="Unerring magic strikes home.",
    )


def heal(caster: Character, target: Character, action: Action) -> ResolutionResult:
    """Resolve a healing action (e.g. Cure Wounds)."""
    assert action.heal is not None
    dr = dice.roll_dice(action.heal.count, action.heal.sides, action.heal.bonus)
    deltas = apply_healing(target, dr.total)
    return ResolutionResult(
        kind=ResultKind.HEAL,
        actor_id=caster.id,
        actor_name=caster.name,
        target_id=target.id,
        target_name=target.name,
        success=True,
        healing=dr.total,
        roll_breakdown=f"{action.name}: {dr.breakdown()}",
        deltas=deltas,
        summary=f"{caster.name} uses {action.name} on {target.name}: heals {dr.total} HP",
        narration_hint="Warm restorative energy mends wounds.",
    )


# ───────────────────────── Initiative & death saves ─────────────────────────
def roll_initiative(character: Character) -> int:
    return dice.roll_d20(character.mod(Ability.DEX)).total


def roll_death_save(pc: Character) -> ResolutionResult:
    """5e death saving throw for a downed PC. 3 successes = stable, 3 failures = dead."""
    roll = dice.roll_d20(0)
    deltas: list[str] = []
    if roll.is_crit:
        pc.hp = 1
        pc.death_successes = pc.death_failures = 0
        if "unconscious" in pc.conditions:
            pc.conditions.remove("unconscious")
        outcome = "natural 20 — regains 1 HP and revives!"
    elif roll.is_fumble:
        pc.death_failures += 2
        outcome = "natural 1 — two failures!"
    elif roll.total >= 10:
        pc.death_successes += 1
        outcome = "success"
    else:
        pc.death_failures += 1
        outcome = "failure"

    if pc.death_failures >= 3:
        _mark_dead(pc)
        deltas.append(f"{pc.name} has failed three death saves and dies")
    elif pc.death_successes >= 3:
        if "stable" not in pc.conditions:
            pc.conditions.append("stable")
        deltas.append(f"{pc.name} is stable (unconscious but no longer dying)")

    return ResolutionResult(
        kind=ResultKind.SAVE,
        actor_id=pc.id,
        actor_name=pc.name,
        success=outcome in ("success",) or roll.is_crit,
        roll_breakdown=f"Death save: {roll.breakdown()}",
        natural=roll.natural,
        deltas=deltas,
        summary=f"{pc.name} death save: {outcome} "
        f"({pc.death_successes}✓/{pc.death_failures}✗)",
        narration_hint="A fragile, fading moment between life and death.",
    )
