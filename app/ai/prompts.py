"""System prompts and context builders for the intent-parser and the narrator."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import settings
from ..db import store
from ..logging_setup import truncate
from .schemas import ALLOWED_SKILLS, EXTRACT_JSON_SHAPE, INTENT_JSON_SHAPE

if TYPE_CHECKING:
    from ..engine.types import Character, ResolutionResult
    from ..state.game_state import GameState


# Chinese labels for entity state markers shown to the narrator.
_DISPOSITION_ZH = {
    "friendly": "友善", "neutral": "中立", "wary": "戒備",
    "afraid": "驚懼", "hostile": "敵意", "cowed": "屈服",
}
_STATUS_ZH = {
    "present": "在場", "departed": "已離開", "hidden": "躲藏",
    "dead": "死亡", "destroyed": "已毀壞", "unknown": "不明",
}


def _entity_label(e: dict) -> str:
    bits = []
    if e.get("disposition"):
        bits.append(_DISPOSITION_ZH.get(e["disposition"], e["disposition"]))
    bits.append(_STATUS_ZH.get(e.get("status", "present"), e["status"]))
    tag = "，".join(bits)
    line = f"- {e['name']}（{tag}）"
    if e.get("notes"):
        line += f"：{truncate(e['notes'], 80)}"
    return line


def compose_scene_summary(state: "GameState") -> str:
    """Dynamic scene summary (replaces the static re-injected one). Built from the
    base backstory plus LIVE entity state, so a departed NPC stops being described
    as present and cannot silently reappear."""
    scene_id = state.scene.id
    ss = None
    try:
        ss = store.get_scene_state(scene_id)
    except Exception:  # noqa: BLE001
        ss = None
    base = (ss or {}).get("base_summary") or state.scene.summary

    all_entities = state.all_entities()
    present = [e for e in all_entities if e["status"] not in store._ABSENT_STATUSES]
    absent = [e for e in all_entities if e["status"] in store._ABSENT_STATUSES]

    parts = [base]
    if present:
        parts.append("目前在場：\n" + "\n".join(_entity_label(e) for e in present))
    if absent:
        gone = "、".join(f"{e['name']}（{_STATUS_ZH.get(e['status'], e['status'])}）" for e in absent)
        parts.append(f"已不在場（不要再描述他們出現在此場景）：{gone}")
    return "\n".join(parts)


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

For tier "A", also decide `needs_check`:
- Set `needs_check` FALSE only for trivial, uncontested, no-risk actions where failure \
makes no sense and nothing opposes the actor — e.g. walking somewhere safe, picking up \
an unguarded object, sitting down, glancing around an open room, or casual small-talk \
with a friendly character. These simply happen; no dice.
- Set `needs_check` TRUE whenever there is any opposition, risk, a meaningful chance of \
failure, a guarded/wary/hostile target, or a contested skill. Attacking, sneaking, \
persuading, deceiving, intimidating, and picking locks ALWAYS need a check.
- When unsure, set `needs_check` TRUE. The program may still force a check; it never \
forces a free success.

Set `implausible` TRUE when the message depends on a FALSE PREMISE: equipment the ACTOR \
does not carry (check the actor's INVENTORY), or a fact/object/person not established in \
the SCENE. Example: "I detonate the C4 I hid in the mine" when the actor has no explosives \
and no such thing is in the scene. When `implausible` is true, do NOT invent an options \
menu that treats the false premise as real — the program will gently redirect the player. \
You may still fill `action`/`target` with the player's apparent goal (e.g. action="enter", \
target="the mine") so the redirect can suggest legitimate approaches. Possessing an item \
in INVENTORY is NOT implausible. A merely risky-but-possible action is NOT implausible.

Set `is_attack` true ONLY if the player is trying to physically attack/fight someone.
Only set `suggested_dc` (one of 5/10/15/20/25/30/35) for unusual actions not covered \
by the scene; otherwise leave it null and let the program decide.
Write all player-facing `question`, `candidates`, and `options` in Traditional Chinese.

Respond with ONLY a JSON object of this exact shape (no prose, no markdown fences):
{INTENT_JSON_SHAPE}"""


def intent_context(state: "GameState", actor: "Character", text: str) -> str:
    skills = ", ".join(sorted(actor.skill_prof.keys())) or "no special training"
    # Same source as the narrator (compose_scene_summary + live present entities), so the
    # parser can no longer be fed a stale scripted blurb while the fiction has moved on.
    present = [e for e in state.present_entities() if e.get("kind") != "location"]
    npcs = ", ".join(e["name"] for e in present) if present else "none notable"
    table = ", ".join(f"{k} (DC {v})" for k, v in state.scene.challenges.items()) or "none predefined"
    in_combat = "YES — this is a combat turn." if (state.combat and state.combat.active) else "no"
    carried = "、".join(actor.inventory) if getattr(actor, "inventory", None) else "（無特別裝備）"
    return (
        f"SCENE: {state.scene.title}\n{compose_scene_summary(state)}\n"
        f"NPCs/targets present: {npcs}\n"
        f"Known scene checks: {table}\n"
        f"In combat: {in_combat}\n"
        f"ACTOR: {actor.name} (proficient skills: {skills})\n"
        f"ACTOR INVENTORY: {carried}\n"
        f"PLAYER MESSAGE: \"{text}\""
    )


# ───────────────────────── Narrator (GM voice) ─────────────────────────
NARRATE_SYSTEM = """You are the GAME MASTER narrator for a Dungeons & Dragons 5e session. \
You will be given a STRUCTURED RESULT that the game engine has already computed (dice, \
hits, damage, band, cost). Your job is to dramatize it in vivid, concise prose.

ABSOLUTE RULES:
- Write ONLY in Traditional Chinese.
- Never contradict or change any number, hit/miss, success/failure, band, cost, or HP \
in the result.
- Never invent new mechanical outcomes (no extra damage, no new enemies dying, no loot \
unless stated).
- If the result has a `band` of PARTIAL: the action DID succeed — narrate the goal as \
achieved, then weave the given cost (e.g. exposure / time / relation) into the same \
moment as a complication. Do not flip it into a failure.
- If a `cost` is provided, the TYPE and SEVERITY come from the engine — honour both. \
You may colour the cost with sensory detail, but you may not switch its category or \
add a different cost.
- Keep it to 1-3 sentences. Be evocative but tight. Address the table naturally.
- Do not mention dice, DCs, or modifiers explicitly unless it adds flavor; describe the \
fiction, not the math."""


def _event_line(e) -> str:
    """Format one past event as `- <actor> → <target>: <summary>` plus the prose that
    was narrated for it. Feeding the PROSE back (not just the mechanical summary) is
    what lets the narrator stay consistent with established fiction."""
    actor = e.actor_name
    target = (e.data or {}).get("target_name") if hasattr(e, "data") else None
    head = f"{actor} → {target}" if target else actor
    line = f"- {head}: {e.summary}"
    prose = getattr(e, "narration", "")
    if prose:
        line += f"\n  敘事：{truncate(prose, 160)}"
    return line


def _entities_block(state: "GameState") -> str:
    """Structured 'who/what is here' the narrator must not contradict (§8.0)."""
    all_entities = state.all_entities()
    present = [e for e in all_entities if e["status"] not in store._ABSENT_STATUSES]
    absent = [e for e in all_entities if e["status"] in store._ABSENT_STATUSES]
    lines = []
    if present:
        lines.append("IN THE SCENE NOW:\n" + "\n".join(_entity_label(e) for e in present))
    if absent:
        gone = "、".join(e["name"] for e in absent)
        lines.append(f"NO LONGER PRESENT (do NOT bring them back into the scene): {gone}")
    return "\n".join(lines)


def narrate_context(state: "GameState", result: "ResolutionResult") -> str:
    window = settings.narrate_context_window
    recent = state.event_log[-window:-1] if len(state.event_log) > 1 else []
    history = "\n".join(_event_line(e) for e in recent) or "- (scene just beginning)"
    entities = _entities_block(state)
    parts = [
        f"SCENE: {state.scene.title}\n{compose_scene_summary(state)}",
    ]
    if entities:
        parts.append(entities)
    parts += [
        f"RECENT EVENTS:\n{history}",
        "STRUCTURED RESULT (do not alter any of this):",
        f"  actor: {result.actor_name}",
    ]
    # Critical: without target the model can't tell who the action is aimed at and
    # picks a random NPC from the scene description.
    if result.target_name:
        parts.append(f"  target: {result.target_name}")
    # The player's original utterance is the strongest signal of intent — pass it
    # verbatim so the narrator stays on the right beat (商人 vs 兜帽客 etc.).
    if result.raw_text:
        parts.append(f"  player said (verbatim, do not translate or invent): \"{result.raw_text}\"")
    parts.append(f"  mechanical summary: {result.summary}")
    if result.band:
        parts.append(f"  band: {result.band.value} (SUCCESS=clean, PARTIAL=succeeded with cost, FAILURE=did not achieve)")
    if result.cost:
        parts.append(
            f"  cost: type={result.cost.type.value}, severity={result.cost.severity.value}"
            + (f" — {result.cost.note}" if result.cost.note else "")
        )
    if result.roll_breakdown:
        parts.append(f"  roll: {result.roll_breakdown}")
    if result.deltas:
        parts.append("  state changes: " + "; ".join(result.deltas))
    if result.narration_hint:
        parts.append(f"  tone hint: {result.narration_hint}")
    parts.append(
        "\nWrite the narration now in Traditional Chinese (1-3 sentences). "
        "The actor MUST be the one named above; if a target is given, the action MUST be "
        "directed at that target — do not substitute a different NPC from the scene."
    )
    return "\n".join(parts)


# ───────────────────────── Narrator (no-roll beat) ─────────────────────────
NARRATE_BEAT_SYSTEM = """You are the GAME MASTER narrating a brief, uncontested action \
in a Dungeons & Dragons 5e session. There was NO dice roll — this action simply happens. \
Describe the moment: what the actor does and its immediate, mundane result.

ABSOLUTE RULES:
- Write ONLY in Traditional Chinese.
- Nothing is at stake: do NOT introduce success/failure, danger, dice, DCs, damage, or \
any new mechanical outcome.
- Do NOT invent new enemies, loot, or consequences; stay within the established scene.
- Keep it to 1-2 sentences. Be evocative but tight."""


# ───────────────────────── Scene opener ─────────────────────────
SCENE_SYSTEM = """You are the GAME MASTER opening a scene in a D&D 5e one-shot. Set the \
scene in 2-4 atmospheric sentences based on the provided summary. Do not resolve any \
actions or invent mechanics; just paint the picture and invite the players to act.
Write ONLY in Traditional Chinese."""


def scene_context(state: "GameState") -> str:
    npcs = ", ".join(state.scene.npcs) if state.scene.npcs else "none"
    return (
        f"SCENE TITLE: {state.scene.title}\n"
        f"SUMMARY: {state.scene.summary}\n"
        f"NPCs present: {npcs}\n"
        "Open the scene now in Traditional Chinese:"
    )


# ───────────────────────── Entity-state extractor ─────────────────────────
EXTRACT_SYSTEM = f"""You are a STATE EXTRACTOR for a tabletop RPG engine. You read one \
GM narration and the list of KNOWN ENTITIES, then report ONLY changes to the entities' \
narrative state — who left or arrived, who changed attitude, or a brand-new entity that \
clearly appeared.

ABSOLUTE RULES:
- NEVER report numbers, HP, damage, dice, DCs, or success/failure — those belong to the \
engine, not you.
- Only emit a delta when the narration makes the change clear. If nothing changed, \
return {{"deltas": []}}.
- For `entity_ref`, reuse the exact name or an alias from KNOWN ENTITIES. Do not rename \
existing entities.
- Set `status` to "departed" when someone leaves/flees/exits the scene, "dead" when they \
die, "hidden" when they conceal themselves. Set `disposition` only when their attitude \
clearly shifts.
- Set `register_kind`/`register_name` for anything genuinely NEW and not already listed: \
a person/creature/object that appears, OR a named place the narration introduces (use \
`register_kind`:"location"). Reporting a new place is just a candidate — the engine \
decides when it becomes permanent, so report it whenever it is clearly named.

Respond with ONLY a JSON object of this exact shape (no prose, no markdown fences):
{EXTRACT_JSON_SHAPE}"""


def extract_context(state: "GameState", prose: str) -> str:
    known = state.all_entities()
    if known:
        lines = "\n".join(
            f"- {e['name']} (aliases: {', '.join(e.get('aliases') or []) or '—'}) "
            f"— status={e['status']}, disposition={e.get('disposition') or '—'}"
            for e in known
        )
    else:
        lines = "（尚無已知實體）"
    return (
        f"KNOWN ENTITIES:\n{lines}\n\n"
        f"GM NARRATION:\n{prose}\n\n"
        "Extract entity-state changes as JSON:"
    )
