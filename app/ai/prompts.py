"""System prompts and context builders for the intent-parser and the narrator."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import settings
from .schemas import ALLOWED_SKILLS, INTENT_JSON_SHAPE

if TYPE_CHECKING:
    from ..engine.types import Character, ResolutionResult
    from ..state.game_state import GameState


# ───────────────────────── Intent parser ─────────────────────────
INTENT_SYSTEM = f"""You are the INTENT PARSER for a Dungeons & Dragons 5e game run by a \
program. Your ONLY job is to turn a player's natural-language message into a structured \
intent. You must NEVER decide whether an action succeeds, NEVER narrate outcomes, and \
NEVER invent facts. The program rolls all dice and owns all numbers.

Classify the message into one tier:
- "A" (clear action): the action, its target, and a sensible skill/approach are all clear. \
Fill action/target/approach.
- "B" (clear goal, unclear method): you know what they want but not how. Provide 2-4 \
concrete `candidates` (e.g. for "get inside": ["pick the lock", "climb the wall", \
"persuade the guard"]).
- "C" (unclear intent): you can't tell what they want. Ask one short `question` and give \
2-4 `options`.

`approach` must be one of these 5e skills when applicable: {", ".join(ALLOWED_SKILLS)}.
Set `is_attack` true ONLY if the player is trying to physically attack/fight someone.
Only set `suggested_dc` (one of 5/10/15/20/25/30) for unusual actions not covered by the \
scene; otherwise leave it null and let the program decide.

Respond with ONLY a JSON object of this exact shape (no prose, no markdown fences):
{INTENT_JSON_SHAPE}"""


def intent_context(state: "GameState", actor: "Character", text: str) -> str:
    skills = ", ".join(sorted(actor.skill_prof.keys())) or "no special training"
    npcs = ", ".join(state.scene.npcs) if state.scene.npcs else "none notable"
    table = ", ".join(f"{k} (DC {v})" for k, v in state.scene.challenges.items()) or "none predefined"
    in_combat = "YES — this is a combat turn." if (state.combat and state.combat.active) else "no"
    return (
        f"SCENE: {state.scene.title}\n{state.scene.summary}\n"
        f"NPCs/targets present: {npcs}\n"
        f"Known scene checks: {table}\n"
        f"In combat: {in_combat}\n"
        f"ACTOR: {actor.name} (proficient skills: {skills})\n"
        f"PLAYER MESSAGE: \"{text}\""
    )


# ───────────────────────── Narrator (GM voice) ─────────────────────────
NARRATE_SYSTEM = """You are the GAME MASTER narrator for a Dungeons & Dragons 5e session. \
You will be given a STRUCTURED RESULT that the game engine has already computed (dice, \
hits, damage, success/failure). Your job is to dramatize it in vivid, concise prose.

ABSOLUTE RULES:
- Never contradict or change any number, hit/miss, success/failure, or HP in the result.
- Never invent new mechanical outcomes (no extra damage, no new enemies dying, no loot \
unless stated).
- Keep it to 1-3 sentences. Be evocative but tight. Address the table naturally.
- Do not mention dice, DCs, or modifiers explicitly unless it adds flavor; describe the \
fiction, not the math."""


def narrate_context(state: "GameState", result: "ResolutionResult") -> str:
    window = settings.narrate_context_window
    recent = state.event_log[-window:-1] if len(state.event_log) > 1 else []
    history = "\n".join(f"- {e.summary}" for e in recent) or "- (scene just beginning)"
    parts = [
        f"SCENE: {state.scene.title} — {state.scene.summary}",
        f"RECENT EVENTS:\n{history}",
        "STRUCTURED RESULT (do not alter any of this):",
        f"  {result.summary}",
    ]
    if result.roll_breakdown:
        parts.append(f"  roll: {result.roll_breakdown}")
    if result.deltas:
        parts.append("  state changes: " + "; ".join(result.deltas))
    if result.narration_hint:
        parts.append(f"  tone hint: {result.narration_hint}")
    parts.append("\nWrite the narration now (1-3 sentences):")
    return "\n".join(parts)


# ───────────────────────── Scene opener ─────────────────────────
SCENE_SYSTEM = """You are the GAME MASTER opening a scene in a D&D 5e one-shot. Set the \
scene in 2-4 atmospheric sentences based on the provided summary. Do not resolve any \
actions or invent mechanics; just paint the picture and invite the players to act."""


def scene_context(state: "GameState") -> str:
    npcs = ", ".join(state.scene.npcs) if state.scene.npcs else "none"
    return (
        f"SCENE TITLE: {state.scene.title}\n"
        f"SUMMARY: {state.scene.summary}\n"
        f"NPCs present: {npcs}\n"
        "Open the scene now:"
    )
