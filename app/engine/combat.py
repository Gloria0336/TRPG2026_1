"""Turn-based 5e combat (design §10, simplified to synchronous play).

Implements the core 5e combat loop: roll initiative, ordered turns, action economy
(one action + one bonus action per turn; movement abstracted), attack vs AC, damage,
saving-throw spells, healing, downed-PC death saves, and victory/defeat detection.
A lightweight monster AI resolves NPC turns automatically.

Positioning, reactions, and opportunity attacks are intentionally out of MVP scope.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from . import dice, rules_5e
from .types import (
    Action,
    ActionCost,
    ActionType,
    Character,
    CombatState,
    ResolutionResult,
    ResultKind,
)

if TYPE_CHECKING:
    from ..state.game_state import GameState


class CombatError(Exception):
    """Raised on an illegal combat action (wrong turn, no economy left, bad target)."""


# ───────────────────────── Setup ─────────────────────────
def start_combat(state: "GameState", participant_ids: list[str]) -> CombatState:
    """Roll initiative for all participants and build the turn order."""
    rolls: list[tuple[str, int]] = []
    for cid in participant_ids:
        ch = state.characters[cid]
        if ch.is_dead:
            continue
        init = rules_5e.roll_initiative(ch)
        rolls.append((cid, init))
    # Sort by initiative desc; ties broken by DEX mod then random for stability.
    rolls.sort(key=lambda t: (t[1], state.characters[t[0]].mod("DEX"), random.random()), reverse=True)

    combat = CombatState(order=rolls, round=1, turn_index=0, active=True)
    state.combat = combat
    order_str = ", ".join(f"{state.characters[cid].name}({init})" for cid, init in rolls)
    state.add_system_event("combat", f"Combat begins! Initiative: {order_str}")
    return combat


def participants(state: "GameState") -> list[Character]:
    return [state.characters[cid] for cid, _ in state.combat.order if cid in state.characters]


def current_combatant(state: "GameState") -> Character | None:
    cid = state.combat.current_id if state.combat else None
    return state.characters.get(cid) if cid else None


def living_enemies(state: "GameState", of: Character) -> list[Character]:
    return [c for c in participants(state) if c.is_pc != of.is_pc and not c.is_down and not c.is_dead]


def living_allies(state: "GameState", of: Character) -> list[Character]:
    return [c for c in participants(state) if c.is_pc == of.is_pc and not c.is_dead]


# ───────────────────────── Turn flow ─────────────────────────
def _reset_turn_economy(combat: CombatState) -> None:
    combat.action_used = False
    combat.bonus_used = False


def advance_turn(state: "GameState") -> Character | None:
    """Advance to the next living combatant, looping rounds. Returns the new actor."""
    combat = state.combat
    if not combat or not combat.active:
        return None
    n = len(combat.order)
    for _ in range(n):
        combat.turn_index += 1
        if combat.turn_index % n == 0:
            combat.round += 1
        _reset_turn_economy(combat)
        actor = current_combatant(state)
        if actor and not actor.is_dead:
            return actor
    return None


def check_combat_end(state: "GameState") -> str | None:
    """Return 'victory' (all monsters down/dead), 'defeat' (all PCs down/dead), or None."""
    combat = state.combat
    if not combat:
        return None
    pcs = [c for c in participants(state) if c.is_pc]
    npcs = [c for c in participants(state) if not c.is_pc]
    if npcs and all(c.is_down or c.is_dead for c in npcs):
        return "victory"
    if pcs and all(c.is_down or c.is_dead for c in pcs):
        return "defeat"
    return None


def _end_combat(state: "GameState", outcome: str) -> None:
    state.combat.active = False
    state.combat.outcome = outcome
    msg = "The party is victorious!" if outcome == "victory" else "The party has fallen..."
    state.add_system_event("combat", f"Combat ends — {outcome}. {msg}")


# ───────────────────────── Resolving a single action ─────────────────────────
def _resolve_one(state: "GameState", actor: Character, action: Action, target: Character | None) -> ResolutionResult:
    if action.type in (ActionType.MELEE_ATTACK, ActionType.RANGED_ATTACK):
        if target is None:
            raise CombatError(f"{action.name} needs a target.")
        return rules_5e.attack(actor, target, action)
    if action.type is ActionType.SAVE_SPELL:
        if target is None:
            raise CombatError(f"{action.name} needs a target.")
        return rules_5e.save_spell(actor, target, action)
    if action.type is ActionType.AUTO_HIT:
        if target is None:
            raise CombatError(f"{action.name} needs a target.")
        return rules_5e.auto_hit(actor, target, action)
    if action.type is ActionType.HEAL:
        tgt = target or actor
        return rules_5e.heal(actor, tgt, action)
    # UTILITY
    return ResolutionResult(
        kind=ResultKind.NARRATIVE,
        actor_id=actor.id,
        actor_name=actor.name,
        summary=f"{actor.name} uses {action.name}",
        narration_hint=action.description or "A tactical maneuver.",
    )


def take_action(
    state: "GameState",
    actor_id: str,
    action_name: str,
    target_ref: str | None = None,
) -> ResolutionResult:
    """Resolve the current combatant's chosen action. Enforces turn + action economy."""
    combat = state.combat
    if not combat or not combat.active:
        raise CombatError("No active combat.")
    actor = current_combatant(state)
    if actor is None or actor.id != actor_id:
        whose = actor.name if actor else "nobody"
        raise CombatError(f"It is not your turn (current turn: {whose}).")
    if actor.is_down:
        raise CombatError(f"{actor.name} is down and cannot take actions.")

    action = actor.find_action(action_name)
    if action is None:
        opts = ", ".join(a.name for a in actor.actions)
        raise CombatError(f"{actor.name} has no action '{action_name}'. Options: {opts}")

    # Action economy.
    if action.cost is ActionCost.ACTION and combat.action_used:
        raise CombatError(f"{actor.name} has already used their action this turn.")
    if action.cost is ActionCost.BONUS and combat.bonus_used:
        raise CombatError(f"{actor.name} has already used their bonus action this turn.")
    if action.uses is not None and action.uses <= 0:
        raise CombatError(f"{action.name} has no uses remaining.")

    target = _pick_target(state, actor, action, target_ref)
    result = _resolve_one(state, actor, action, target)

    # Spend resources.
    if action.cost is ActionCost.ACTION:
        combat.action_used = True
    elif action.cost is ActionCost.BONUS:
        combat.bonus_used = True
    if action.uses is not None:
        action.uses -= 1

    state.log_result(result)
    _post_action(state)
    return result


def _pick_target(state: "GameState", actor: Character, action: Action, target_ref: str | None) -> Character | None:
    if action.type is ActionType.HEAL:
        if target_ref:
            return state.find_character(target_ref)
        # default: the most-hurt living ally (including self)
        allies = living_allies(state, actor)
        return min(allies, key=lambda c: c.hp / max(1, c.max_hp)) if allies else actor
    if action.type is ActionType.UTILITY:
        return state.find_character(target_ref) if target_ref else None
    # offensive
    if target_ref:
        t = state.find_character(target_ref)
        if t is None:
            raise CombatError(f"No target matching '{target_ref}'.")
        return t
    enemies = living_enemies(state, actor)
    if not enemies:
        raise CombatError("No valid enemy targets remain.")
    if len(enemies) == 1:
        return enemies[0]
    raise CombatError("Multiple enemies — specify a target.")


def _post_action(state: "GameState") -> None:
    """After any action: check for combat end."""
    outcome = check_combat_end(state)
    if outcome:
        _end_combat(state, outcome)


# ───────────────────────── Downed PC turns ─────────────────────────
def take_death_save(state: "GameState", actor_id: str) -> ResolutionResult:
    """A downed PC's turn: roll a death save, then end the turn."""
    actor = current_combatant(state)
    if actor is None or actor.id != actor_id:
        raise CombatError("Not this character's turn.")
    if not actor.is_down or actor.is_dead:
        raise CombatError(f"{actor.name} is not making death saves.")
    result = rules_5e.roll_death_save(actor)
    state.combat.action_used = True
    state.log_result(result)
    _post_action(state)
    return result


# ───────────────────────── Monster AI ─────────────────────────
def run_monster_turn(state: "GameState") -> list[ResolutionResult]:
    """Resolve an NPC combatant's whole turn automatically. Returns the results."""
    combat = state.combat
    actor = current_combatant(state)
    results: list[ResolutionResult] = []
    if actor is None or actor.is_pc:
        return results
    if actor.is_dead or actor.is_down:
        return results

    enemies = living_enemies(state, actor)
    if not enemies:
        _post_action(state)
        return results

    # Simple AI: pick a random living enemy (spreads damage — kinder for a newbie
    # one-shot than focus-firing) and use the best available attack.
    target = random.choice(enemies)
    action = _best_attack(actor)
    if action is None:
        return results

    result = _resolve_one(state, actor, action, target)
    combat.action_used = True
    if action.uses is not None:
        action.uses -= 1
    state.log_result(result)
    results.append(result)
    _post_action(state)
    return results


def _best_attack(actor: Character) -> Action | None:
    attacks = [
        a for a in actor.actions
        if a.type in (ActionType.MELEE_ATTACK, ActionType.RANGED_ATTACK, ActionType.SAVE_SPELL, ActionType.AUTO_HIT)
        and a.cost is ActionCost.ACTION
        and (a.uses is None or a.uses > 0)
    ]
    if not attacks:
        return None

    def avg_damage(a: Action) -> float:
        return sum(d.count * (d.sides + 1) / 2 + d.bonus for d in a.damage)

    return max(attacks, key=avg_damage)
