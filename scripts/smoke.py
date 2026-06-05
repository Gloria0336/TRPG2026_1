"""Offline end-to-end smoke test — runs the full pipeline without Discord.

Usage:  python -m scripts.smoke
Feeds canned player inputs through interpret -> resolve/combat -> narrate and prints the
log. Uses the AI if OPENROUTER_API_KEY is set, otherwise the offline fallbacks.
"""
from __future__ import annotations

import asyncio
import sys

# The console may be cp950 on Windows; game text/summaries use UTF-8 (✓, em dashes…).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

from app.ai import orchestrator
from app.config import settings
from app.content import scenario
from app.engine import combat, resolution
from app.engine.types import IntentTier
from app.state import game_state


def banner(text: str) -> None:
    print("\n" + "=" * 70 + f"\n{text}\n" + "=" * 70)


async def do_action(gs: game_state.GameState, actor_id: str, text: str) -> None:
    actor = gs.characters[actor_id]
    print(f"\n>>> {actor.name}: \"{text}\"")
    intent, proposed_dc = await orchestrator.interpret(gs, actor_id, text)

    if intent.tier is IntentTier.B:
        print(f"    [Tier B] GM offers methods: {intent.candidates}")
        return
    if intent.tier is IntentTier.C:
        print(f"    [Tier C] GM asks: {intent.question} {intent.options}")
        return

    if gs.combat and gs.combat.active:
        result = combat.take_action(gs, actor_id, _combat_action(gs, actor_id), intent.target)
    else:
        result = resolution.resolve(gs, intent, proposed_dc=proposed_dc)

    prose = await orchestrator.narrate(gs, result)
    gs.set_narration(gs.event_log[-1].id, prose)
    print(f"    RESULT: {result.summary}")
    if result.roll_breakdown:
        print(f"    ROLL:   {result.roll_breakdown}")
    print(f"    GM:     {prose}")


def _combat_action(gs: game_state.GameState, actor_id: str) -> str:
    actor = gs.characters[actor_id]
    # Just use the first ACTION-cost attack for the smoke run.
    return actor.actions[0].name


async def run_combat(gs: game_state.GameState) -> None:
    rounds = 0
    while gs.combat and gs.combat.active and rounds < 40:
        actor = combat.current_combatant(gs)
        if actor is None:
            break
        if actor.is_pc and not actor.is_down:
            target = next((c for c in combat.living_enemies(gs, actor)), None)
            if target:
                result = combat.take_action(gs, actor.id, actor.actions[0].name, target.id)
                prose = await orchestrator.narrate(gs, result)
                print(f"    {result.summary}\n      → {prose}")
        elif actor.is_pc and actor.is_down:
            result = combat.take_death_save(gs, actor.id)
            print(f"    {result.summary}")
        else:
            for result in combat.run_monster_turn(gs):
                print(f"    {result.summary}")
        combat.advance_turn(gs)
        rounds += 1
    print(f"\n    >>> Combat outcome: {gs.combat.outcome if gs.combat else 'n/a'}")


async def main() -> None:
    if not settings.openrouter_api_key:
        settings.ai_offline = True
        print("(no OPENROUTER_API_KEY — running in offline mode)")

    gs = game_state.reset_state(channel_id=0)

    banner(f"SCENE 1 — {gs.scene.title}")
    print(await orchestrator.open_scene(gs))
    await do_action(gs, "pc_lyra", "I buy Old Perrin a drink and persuade him to tell me everything about the caravan")
    await do_action(gs, "pc_bram", "I scan the tavern for anyone listening in")
    await do_action(gs, "pc_bram", "uhh I dunno")  # should hit Tier B/C

    gs.goto_scene(scenario.scene_by_id("east_road"))
    banner(f"SCENE 2 — {gs.scene.title}")
    print(await orchestrator.open_scene(gs))
    await do_action(gs, "pc_bram", "I search the wrecked wagons for clues")
    await do_action(gs, "pc_lyra", "I follow the tracks leading away from the road")

    # ambush was removed; warren now carries the encounter (scenes project from locations).
    gs.goto_scene(scenario.scene_by_id("warren"))
    banner(f"SCENE 3 — {gs.scene.title} (COMBAT)")
    print(await orchestrator.open_scene(gs))
    gs.start_scene_combat()
    await run_combat(gs)

    banner("EVENT LOG SUMMARY")
    print(f"Total events logged: {len(gs.event_log)}")
    gs.save()
    print(f"Snapshot written to: {settings.session_path}")
    await orchestrator.aclose()


if __name__ == "__main__":
    asyncio.run(main())
