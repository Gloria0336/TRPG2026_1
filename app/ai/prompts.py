"""System prompts and context builders for the intent-parser and the narrator."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import settings
from ..db import store
from ..engine import conditions as cond
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


def current_location_label(state: "GameState") -> str:
    """Human-readable label for the party's current place. Prefers the live location
    entity name (clean — "鎏金酒杯酒館") over the authored scene title ("場景 1：鎏金
    酒杯酒館") so AI prompts read like a place, not a script chapter."""
    try:
        loc = store.get_entity_by_id(state.current_location_id)
        if loc and loc.get("name"):
            return loc["name"]
    except Exception:  # noqa: BLE001 — memory layer must never break prompts
        pass
    return state.scene.title


def known_exits(state: "GameState", *, limit: int = 8) -> list[dict]:
    """Locations the party could plausibly travel to next. Excludes the current
    location; locations are global, so this is the full registry minus 'here'."""
    try:
        all_locs = store.get_locations()
    except Exception:  # noqa: BLE001
        return []
    here = state.current_location_id
    return [e for e in all_locs if e["id"] != here][:limit]


def _entity_conditions(e: dict) -> list[str]:
    flags = e.get("flags") or {}
    raw = flags.get("conditions") if isinstance(flags, dict) else None
    return [c for c in (raw or []) if isinstance(c, str)]


def _entity_label(e: dict) -> str:
    bits = []
    if e.get("disposition"):
        bits.append(_DISPOSITION_ZH.get(e["disposition"], e["disposition"]))
    bits.append(_STATUS_ZH.get(e.get("status", "present"), e["status"]))
    conds = _entity_conditions(e)
    if conds:
        bits.append("狀態：" + "、".join(cond.label(c) for c in conds))
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
INTENT_SYSTEM = f"""You are the INTENT PARSER for a living_world game run by a \
program. Your ONLY job is to turn a player's natural-language message into a structured \
intent. You must NEVER decide whether an action succeeds, NEVER narrate outcomes, and \
NEVER invent facts. The program rolls all dice and owns all numbers.

Classify the message into one tier:
- "A" (clear action): the action, its target, and a sensible skill/approach are all clear. \
Fill action/target/approach. Optionally fill `topic` with the specific subject of the \
action (the thing being asked about / examined / talked into) when the player named one \
— e.g. action="詢問", target="兜帽客", topic="商隊去向" / "內褲顏色" / "他的名字".
- "B" (clear goal, unclear method): you know what they want but not how. Provide 2-4 \
concrete `candidates` (e.g. for "get inside": ["pick the lock", "climb the wall", \
"persuade the guard"]).
- "C" (unclear intent): you can't tell what they want. Write `question` as a SHORT GM \
follow-up in the GM's narrative voice (1-2 sentences, Traditional Chinese): use an NPC \
reaction, sensory hint, or in-fiction question to invite the player to elaborate. NEVER \
phrase it as "請選擇 A/B/C/D" or list mechanical skills. `options` is OPTIONAL — leave it \
empty by default; only include 2-3 SHORT example phrasings (≤8 chars each) when the player \
clearly needs a hint. The player will reply with another natural-language /action, and \
you'll re-interpret with that reply added to the CLARIFICATION HISTORY. Aim to converge \
toward tier A within 1-2 rounds.

PLAYER AGENCY (anti-paternalism — read this carefully):
- The player has full AUTHORIAL agency over their character's actions. Silly, embarrassing, \
taboo, off-topic, or risky actions are ALL legitimate so long as the verb + target are \
identifiable from the literal text.
- If the message has an identifiable verb + target, classify as tier A (filling `topic` \
with the specific subject). Do NOT downgrade to tier C just because the action seems \
off-script for the current plot. Do NOT substitute a "more reasonable" or "more relevant" \
action for what the player wrote. The engine handles consequences via cost/band and the \
narrator handles social fallout in prose — that is not your job.
- Examples that LOOK weird but are tier A, not tier C:
  - "詢問兜帽客穿什麼顏色的內褲" → tier A, action="詢問", target="兜帽客", \
approach="persuasion", topic="內褲顏色"
  - "問守衛他穿不穿襪子" → tier A, action="詢問", target="守衛", topic="是否穿襪子"
  - "舔門把" → tier A, action="舔", target="門把", approach="perception" (taste-as-sensing)
  - "向牧師打聽他的私生活" → tier A, action="打聽", target="牧師", topic="私生活"
- Only fall to tier C when the LITERAL text cannot resolve to a verb+target — "我做點事" \
or "嗯..." or a one-word noun with no action.

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

`implausible` is STRICT — only TRUE when the world's STRUCTURED STATE contradicts the \
action. Two narrow cases:
- The action requires equipment the actor's INVENTORY does not list (e.g. "I detonate \
the C4" with no explosives in inventory).
- The action presupposes a target/object/fact not present in the SCENE entities or the \
established fiction (e.g. "I open the chest" with no chest registered, "I kill the \
dragon" with no dragon present).

`implausible` is NOT a vibe check. The following are NOT implausible, no matter how \
strange they read — return tier A and let the engine + narrator handle consequences:
- Embarrassing or taboo asks ("問兜帽客內褲顏色", "問牧師性生活")
- Off-topic actions that don't advance the plot ("舔門把", "在地上打滾")
- Risky or stupid moves ("徒手抓刀刃", "對著火堆深呼吸")
- Insulting an NPC, propositioning an NPC, breaking decorum
- Physically possible but absurd attempts ("跟桌子說話", "對牆壁施展魅惑")

A merely risky-but-possible action is NOT implausible. A socially awkward action is NOT \
implausible. Possessing an item in INVENTORY is NOT implausible. When in doubt, return \
FALSE — the engine will surface natural consequences via DC, cost band, and narration.

When `implausible` IS true, do NOT invent an options menu that treats the false premise \
as real — the program will gently redirect the player. You may still fill `action`/`target` \
with the player's apparent goal (e.g. action="enter", target="the mine") so the redirect \
can suggest legitimate approaches.

Set `is_attack` true ONLY if the player is trying to physically attack/fight someone.
Only set `suggested_dc` (one of 5/10/15/20/25/30/35) for unusual actions not covered \
by the scene; otherwise leave it null and let the program decide.
Write all player-facing `question`, `candidates`, and `options` in Traditional Chinese.

Respond with ONLY a JSON object of this exact shape (no prose, no markdown fences):
{INTENT_JSON_SHAPE}"""


def _condition_brief(present: list[dict]) -> str:
    """Lines like '- 兜帽客：催眠（社交詢問直接回應；無法主動行動）' so the parser
    treats already-controlled NPCs as no-check social targets."""
    lines: list[str] = []
    for e in present:
        conds = _entity_conditions(e)
        if not conds:
            continue
        labels = [
            f"{cond.label(c)}（{cond.CATALOG[c].description_zh}）"
            for c in conds if c in cond.CATALOG
        ]
        if labels:
            lines.append(f"- {e['name']}：" + "；".join(labels))
    return "\n".join(lines)


def _clarification_block(history: list[dict] | None) -> str:
    """Format the open follow-up thread for the parser: each turn = one GM
    question (the prior round's clarification) + the player's reply that came
    after. Lets the parser converge instead of asking the same question twice."""
    if not history:
        return ""
    lines: list[str] = []
    for i, turn in enumerate(history, 1):
        gm = (turn.get("gm") or "").strip()
        pl = (turn.get("player") or "").strip()
        if gm:
            lines.append(f"  round {i} GM: {truncate(gm, 160)}")
        if pl:
            lines.append(f"  round {i} player: {truncate(pl, 160)}")
    if not lines:
        return ""
    return (
        "CLARIFICATION HISTORY (you are mid-conversation — use this to converge to "
        "tier A this round; do NOT repeat the same question):\n" + "\n".join(lines)
    )


def intent_context(
    state: "GameState",
    actor: "Character",
    text: str,
    *,
    clarification: list[dict] | None = None,
) -> str:
    skills = ", ".join(sorted(actor.skill_prof.keys())) or "no special training"
    # Same source as the narrator (compose_scene_summary + live present entities), so the
    # parser can no longer be fed a stale scripted blurb while the fiction has moved on.
    present = [e for e in state.present_entities() if e.get("kind") != "location"]
    npcs = ", ".join(e["name"] for e in present) if present else "none notable"
    table = ", ".join(f"{k} (DC {v})" for k, v in state.scene.challenges.items()) or "none predefined"
    in_combat = "YES — this is a combat turn." if (state.combat and state.combat.active) else "no"
    carried = "、".join(actor.inventory) if getattr(actor, "inventory", None) else "（無特別裝備）"

    # Reachable destinations. Listing aliases too lets the parser map free-text
    # ("酒館"/"鎏金酒杯酒館") onto the same canonical place.
    exits = known_exits(state)
    if exits:
        exit_lines = [
            f"- {e['name']}" + (f"（別名：{', '.join(e['aliases'])}）" if e.get("aliases") else "")
            for e in exits
        ]
        exits_block = (
            "EXITS (places the party can travel to from here; emergent ones not "
            "listed are also allowed):\n" + "\n".join(exit_lines)
        )
    else:
        exits_block = "EXITS: (none recorded yet — naming a new place is fine)"

    parts = [
        f"LOCATION: {current_location_label(state)}",
        compose_scene_summary(state),
        f"NPCs/targets present: {npcs}",
        exits_block,
    ]
    brief = _condition_brief(present)
    if brief:
        parts.append(
            "TARGET CONDITIONS (mechanical — respect them):\n" + brief +
            "\nIf the player addresses a target whose condition makes the check moot "
            "(e.g. hypnotized → social auto-success), set needs_check=false and do NOT "
            "offer威嚇/魅惑/洞察 options for that target."
        )
    parts += [
        f"Known location checks: {table}",
        f"In combat: {in_combat}",
        f"ACTOR: {actor.name} (proficient skills: {skills})",
        f"ACTOR INVENTORY: {carried}",
    ]
    cb = _clarification_block(clarification)
    if cb:
        parts.append(cb)
    parts.append(f"PLAYER MESSAGE: \"{text}\"")
    return "\n".join(parts)


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
        lines.append("HERE NOW:\n" + "\n".join(_entity_label(e) for e in present))
    if absent:
        gone = "、".join(e["name"] for e in absent)
        lines.append(f"NO LONGER PRESENT (do NOT bring them back into this location): {gone}")
    return "\n".join(lines)


def narrate_context(state: "GameState", result: "ResolutionResult") -> str:
    window = settings.narrate_context_window
    recent = state.event_log[-window:-1] if len(state.event_log) > 1 else []
    history = "\n".join(_event_line(e) for e in recent) or "- (location just entered)"
    entities = _entities_block(state)
    parts = [
        f"LOCATION: {current_location_label(state)}\n{compose_scene_summary(state)}",
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
    # When the player named a specific subject, surface it so the prose is literal
    # ("asks about her underwear color") rather than generic ("asks a question").
    if getattr(result, "topic", None):
        parts.append(f"  topic (the specific subject — narrate it literally, do not soften): \"{result.topic}\"")
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


# ───────────────────────── Location opener ─────────────────────────
SCENE_SYSTEM = """You are the GAME MASTER establishing a LOCATION in a D&D 5e one-shot. \
Paint the place in 2-4 atmospheric sentences based on the provided summary. Do not resolve \
any actions or invent mechanics; just describe where the party finds themselves and invite \
them to act. The party arrives at this location naturally as the story unfolds — frame it \
as a place they have walked into, not a scripted scene.
Write ONLY in Traditional Chinese."""


def scene_context(state: "GameState") -> str:
    npcs = ", ".join(state.scene.npcs) if state.scene.npcs else "none"
    return (
        f"LOCATION: {current_location_label(state)}\n"
        f"SUMMARY: {state.scene.summary}\n"
        f"NPCs present: {npcs}\n"
        "Establish this location now in Traditional Chinese:"
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
- Use `add_conditions` / `remove_conditions` ONLY when the narration shows the entity \
*newly* gained or *clearly lost* a mechanical state — e.g. tied up → add ["restrained"], \
the figure awoke → remove ["unconscious", "hypnotized"]. Pick ids from the allowed list \
in the JSON shape; unknown ids are silently dropped. Do NOT re-report conditions the \
entity already has.
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
