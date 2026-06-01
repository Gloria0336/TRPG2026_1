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
    ResultKind,
    SKILLS,
)

# 5e DC anchors (design §4.3 mapped onto standard 5e difficulty labels).
DC_LABELS: dict[int, str] = {
    5: "Very Easy",
    10: "Easy",
    15: "Medium",
    20: "Hard",
    25: "Very Hard",
    30: "Nearly Impossible",
}
DC_ANCHORS: list[int] = sorted(DC_LABELS)


def nearest_anchor(dc: int) -> int:
    """Snap an arbitrary DC to the nearest 5e anchor (used to constrain AI DC proposals)."""
    return min(DC_ANCHORS, key=lambda a: abs(a - dc))


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


def ability_check(
    actor: Character,
    key: str,
    dc: int,
    *,
    advantage: bool = False,
    disadvantage: bool = False,
) -> ResolutionResult:
    """Resolve a skill/ability check vs a DC. Standard 5e: success if total >= DC."""
    bonus, label = check_bonus(actor, key)
    roll = dice.roll_d20(bonus, advantage=advantage, disadvantage=disadvantage)
    success = roll.total >= dc
    # nat 20 / nat 1 add narrative flavour (and a thumb on the scale) per design §4.4.
    if roll.is_crit:
        success = True
    elif roll.is_fumble:
        success = False

    verdict = "SUCCESS" if success else "FAILURE"
    flavour = " (crit!)" if roll.is_crit else " (fumble!)" if roll.is_fumble else ""
    return ResolutionResult(
        kind=ResultKind.CHECK,
        actor_id=actor.id,
        actor_name=actor.name,
        success=success,
        dc=dc,
        roll_breakdown=roll.breakdown(),
        natural=roll.natural,
        crit=roll.is_crit,
        fumble=roll.is_fumble,
        summary=f"{label} check vs DC {dc}: {verdict}{flavour}",
        narration_hint=(
            "Describe a decisive, lucky break." if roll.is_crit
            else "Describe an unlucky complication." if roll.is_fumble
            else "Describe a clean success." if success
            else "Describe a setback; the attempt fails."
        ),
    )


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
