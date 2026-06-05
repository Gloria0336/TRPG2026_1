"""System prompts and context builders for the intent-parser and the narrator."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..config import settings
from ..db import store
from ..discord_bot import i18n
from ..engine import conditions as cond
from ..logging_setup import truncate
from .schemas import (
    ALLOWED_SKILLS,
    EXTRACT_JSON_SHAPE,
    INTENT_JSON_SHAPE,
    LOCATION_CARD_JSON_SHAPE,
    QUEST_DETAILS_JSON_SHAPE,
    QUEST_SEED_JSON_SHAPE,
)

if TYPE_CHECKING:
    from ..engine.types import Character, ResolutionResult
    from ..state.game_state import GameState


# Chinese labels for entity state markers shown to the narrator.
_DISPOSITION_ZH = {
    "friendly": "友善", "neutral": "中立", "wary": "戒備",
    "afraid": "驚懼", "hostile": "敵意", "attack": "發動攻擊", "cowed": "屈服",
}


def disposition_label(disposition: str | None) -> str:
    """Chinese label for an NPC disposition; falls back to the raw value, '' for None."""
    if not disposition:
        return ""
    return _DISPOSITION_ZH.get(disposition, disposition)
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
    """Locations the party could plausibly travel to next. Prefers the current location's
    world-graph neighbours (connects + parent + children); falls back to the full registry
    minus 'here' for emergent places that carry no adjacency, so the player is never stranded."""
    try:
        here_id = state.current_location_id
        exits = store.travel_options(here_id)
        if exits:
            return exits[:limit]
        return [e for e in store.get_locations() if e["id"] != here_id][:limit]
    except Exception:  # noqa: BLE001
        return []


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
    # Rolling digest of story-so-far (written by orchestrator.update_rolling_summary).
    # Remembers plot that has scrolled out of the event window. Skipped when it has not
    # diverged from base yet (fresh scene) to avoid echoing the backstory twice.
    digest = ((ss or {}).get("current_summary") or "").strip()

    all_entities = state.all_entities()
    present = [e for e in all_entities if e["status"] not in store._ABSENT_STATUSES]
    absent = [e for e in all_entities if e["status"] in store._ABSENT_STATUSES]

    parts = [base]
    if digest and digest != (base or "").strip():
        parts.append("至此發生過的關鍵情節（必須與此一致）：\n" + digest)
    # Persistent place-state (tripwire disarmed, water spilled …) survives revisits.
    try:
        place_state = store.location_state_note(state.current_location_id)
    except Exception:  # noqa: BLE001
        place_state = ""
    if place_state:
        parts.append(f"此地已發生、且持續存在的變化：{place_state}")
    if present:
        parts.append("目前在場：\n" + "\n".join(_entity_label(e) for e in present))
    if absent:
        gone = "、".join(f"{e['name']}（{_STATUS_ZH.get(e['status'], e['status'])}）" for e in absent)
        parts.append(f"已不在場（不要再描述他們出現在此場景）：{gone}")
    return "\n".join(parts)


# ───────────────────────── Intent parser ─────────────────────────
def _location_card_block(state: "GameState") -> str:
    """Compact generated location anchors the model must not contradict."""
    try:
        card = store.get_location_card(state.current_location_id)
    except Exception:  # noqa: BLE001
        card = None
    if not card:
        return ""
    lines = [
        "LOCATION CARD ANCHORS (stable generated truth; do not contradict):",
        f"- canonical_name: {card.get('canonical_name') or current_location_label(state)}",
    ]
    if card.get("mood"):
        lines.append(f"- mood: {card['mood']}")
    if card.get("base_summary"):
        lines.append(f"- base_summary: {truncate(card['base_summary'], 300)}")
    for key, label in (
        ("sensory_anchors", "sensory"),
        ("visual_landmarks", "landmarks"),
        ("interactive_features", "interactive"),
        ("discoverables", "discoverables"),
        ("hazards", "hazards"),
        ("soft_hooks", "hooks"),
        ("exits_hint", "exits"),
    ):
        values = card.get(key) or []
        if values:
            lines.append(f"- {label}: " + "; ".join(str(v) for v in values[:4]))
    return "\n".join(lines)


LOCATION_CARD_SYSTEM = f"""You are a LOCATION REGISTRATION AGENT for a living-world Pathfinder 2e engine.

Your job is to turn a location registration request into stable structured world data.
Write all player-facing fields in Traditional Chinese.

Rules:
- Preserve the requested location name exactly unless a canonical authored name is provided.
- Treat authored notes, aliases, parent, connects, current location, recent events, and player wording as hard context.
- Create sensory and exploration anchors that future narration can reuse.
- Do not decide success/failure, DCs, HP, damage, treasure quantities, or mechanical outcomes.
- Set terrain_modifier only as travel friction: 1.0 normal road, below 1.0 rough/difficult, above 1.0 easy/clear. Do not use it for success odds.
- Do not create NPC/entity records. You may mention vague traces, landmarks, hazards, or hooks only as location texture.
- Avoid contradicting existing locations or moving the party.
- Keep the card concise and reusable.

Return ONLY JSON in this exact shape:
{LOCATION_CARD_JSON_SHAPE}"""


def location_card_context(request: dict) -> str:
    existing = request.get("existing_locations") or []
    existing_lines = []
    for loc in existing[:16]:
        aliases = loc.get("aliases") or []
        alias_text = f" (aliases: {', '.join(aliases)})" if aliases else ""
        existing_lines.append(f"- {loc.get('name', '')}{alias_text}")
    recent = request.get("recent_events") or []
    recent_lines = "\n".join(str(e) for e in recent[-6:]) or "- (no recent events)"
    return "\n".join([
        f"REQUESTED_NAME: {request.get('requested_name', '')}",
        f"CANONICAL_NAME: {request.get('canonical_name') or ''}",
        f"SOURCE: {request.get('source', '')}",
        f"CURRENT_LOCATION: {request.get('current_location', '')}",
        f"AUTHORED_NOTES: {request.get('authored_notes') or ''}",
        "ALIASES: " + ", ".join(request.get("aliases") or []),
        f"PARENT: {request.get('parent') or ''}",
        "CONNECTS: " + ", ".join(request.get("connects") or []),
        f"PLAYER_TEXT: {request.get('player_text') or ''}",
        "EXISTING_LOCATIONS:\n" + ("\n".join(existing_lines) or "- none"),
        "RECENT_EVENTS:\n" + recent_lines,
        "Generate the location card JSON now.",
    ])


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
- CONVERGING from tier C: when a CLARIFICATION HISTORY is present, the player's earlier \
lines are their STANDING goal — COMBINE every line in the history with the new message \
into a single tier-A intent rather than asking a further narrowing question. Example: \
history "前往北方" → GM "哪條路線？" → now "跟隨商隊路線" should resolve to tier A \
action="前往"/travel, target="北方"（沿商隊路線）, NOT another "which one?" question. A clear \
movement or action declaration ("前往北方", "跟隨商隊路線", "查看晨橋商隊位置") is already \
tier A — never re-package it into a menu. Once you have asked once and the player has \
answered, you should almost always return tier A.

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
approach="diplomacy", topic="內褲顏色"
  - "問守衛他穿不穿襪子" → tier A, action="詢問", target="守衛", topic="是否穿襪子"
  - "舔門把" → tier A, action="舔", target="門把", approach="perception" (taste-as-sensing)
  - "向牧師打聽他的私生活" → tier A, action="打聽", target="牧師", topic="私生活"
- Only fall to tier C when the LITERAL text cannot resolve to a verb+target — "我做點事" \
or "嗯..." or a one-word noun with no action.

`approach` must be one of these PF2e skills when applicable: {", ".join(ALLOWED_SKILLS)}.

For tier "A", also decide `needs_check`:
- Set `needs_check` FALSE only for trivial, uncontested, no-risk actions where failure \
makes no sense and nothing opposes the actor — e.g. walking somewhere safe, picking up \
an unguarded object, sitting down, glancing around an open room, or casual small-talk \
with a friendly character. These simply happen; no dice.
- Set `needs_check` TRUE whenever there is any opposition, risk, a meaningful chance of \
failure, a guarded/wary/hostile target, or a contested skill. Attacking, sneaking, \
persuading, deceiving, intimidating, and picking locks ALWAYS need a check.
- CONSULTING information you ALREADY hold or that is already revealed is FALSE: reading a \
map you carry, re-reading a sign or notice, glancing at notes you've taken, recalling \
something already established. SEARCHING for HIDDEN or UNKNOWN things is TRUE: spotting a \
trap, finding eavesdroppers, searching wreckage for clues, scanning for what isn't obvious. \
So "查看地圖"（手上已有地圖）→ tier A, needs_check=false; "搜索房間找偷聽者" → needs_check=true.
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
- Pure movement/travel declarations ("前往北方", "跟隨商隊路線前往北方", "往東路走") are \
NEVER implausible — the program's travel system resolves the destination. Return tier A \
(action="前往"/travel, target=the place) with implausible=false, even if the place is not \
yet listed in EXITS.

A merely risky-but-possible action is NOT implausible. A socially awkward action is NOT \
implausible. Possessing an item in INVENTORY is NOT implausible. When in doubt, return \
FALSE — the engine will surface natural consequences via DC, cost band, and narration.

When `implausible` IS true, do NOT invent an options menu that treats the false premise \
as real — the program will gently redirect the player. You may still fill `action`/`target` \
with the player's apparent goal (e.g. action="enter", target="the mine") so the redirect \
can suggest legitimate approaches.

Set `is_attack` true ONLY if the player is trying to physically attack/fight someone.

DIFFICULTY (set this whenever a roll is needed — you own the DC):
- `difficulty_band` rates how hard the player's chosen METHOD is — using the right \
tool/skill is low, brute-forcing or an ill-suited method is high:
  easy(用對方法/有利, DC5) / normal(標準, DC10) / hard(吃力或方法不佳, DC15) / extreme(蠻幹/外行硬上, DC20).
  Example: 用開鎖工具開鎖 → easy; 用腳踹開門 → hard~extreme.
- `env_modifier` is a -4..+4 offset for how hard the SCENE/TARGET itself is — favourable \
conditions go negative, hostile conditions go positive. Example: 普通木門 → about -3; \
監牢大門 → about +4. Put a short reason in `env_reason`.
- The final DC = band base + env_modifier, computed by the program. Do NOT fold tools, \
allies, or spent resources into either value — the program applies those to the player's \
roll, not the DC. Pick the band/modifier on the action's own merits — do NOT default to \
`normal`/0 when the action is clearly easier or harder. Leave `difficulty_band` null (and \
env_modifier 0) ONLY when no roll is needed.
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
    """Format the open follow-up thread for the parser. Each turn = the player's
    utterance that round + the GM follow-up that came after it. Printing the
    player's OWN words (oldest first) keeps the original goal in view so the parser
    merges the whole thread into one intent instead of anchoring on its own last
    question — the root of the "doesn't remember what the player just said" loop."""
    if not history:
        return ""
    lines: list[str] = []
    for i, turn in enumerate(history, 1):
        pl = (turn.get("player") or "").strip()
        gm = (turn.get("gm") or "").strip()
        if pl:
            lines.append(f"  round {i} player: {truncate(pl, 160)}")
        if gm:
            lines.append(f"  round {i} GM: {truncate(gm, 160)}")
    if not lines:
        return ""
    # Lazy import: game_state does not import this module, so no cycle.
    from ..state.game_state import GameState
    this_round = len(history) + 1
    if this_round >= GameState.MAX_CLARIFICATION_TURNS:
        pressure = (
            " This is the FINAL clarification round — you MUST return tier A (or the "
            "closest playable action) now; do NOT ask another question."
        )
    else:
        pressure = (
            " As soon as a verb + target are identifiable from the COMBINED thread, "
            "return tier A NOW — do not ask a further narrowing question."
        )
    return (
        f"CLARIFICATION HISTORY (you are mid-conversation; this is round {this_round}). "
        "Treat every player line above PLUS the message below as ONE accumulating intent "
        "— merge them; do not reset to the latest line alone." + pressure + "\n"
        + "\n".join(lines)
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
    in_combat = "YES — this is a combat turn." if (state.combat and state.combat.active) else "no"
    try:
        projected_inventory = store.project_inventory(actor.id)
    except Exception:  # noqa: BLE001
        projected_inventory = []
    carried_items = projected_inventory or list(getattr(actor, "inventory", None) or [])
    carried = "、".join(carried_items) if carried_items else "（無特別裝備）"

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
    card_block = _location_card_block(state)
    if card_block:
        parts.append(card_block)
    brief = _condition_brief(present)
    if brief:
        parts.append(
            "TARGET CONDITIONS (mechanical — respect them):\n" + brief +
            "\nIf the player addresses a target whose condition makes the check moot "
            "(e.g. hypnotized → social auto-success), set needs_check=false and do NOT "
            "offer威嚇/魅惑/洞察 options for that target."
        )
    parts += [
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
NARRATE_SYSTEM = """You are the GAME MASTER narrator for a Pathfinder 2e session. \
You will be given a STRUCTURED RESULT that the game engine has already computed (dice, \
hits, damage, band, cost, boon). Your job is to dramatize it in vivid, concise prose.

ABSOLUTE RULES:
- Write ONLY in Traditional Chinese.
- Never contradict or change any number, hit/miss, success/failure, band, cost, boon, or \
HP in the result.
- Never invent new mechanical outcomes (no extra damage, no new enemies dying, no loot \
unless stated).
- Honour the four-degree `band` exactly:
  - CRIT_SUCCESS: a decisive, exceptional success — narrate the goal achieved and weave \
in the given `boon` as an extra upside in the same moment.
  - SUCCESS: a clean success.
  - FAILURE: the attempt does NOT achieve its goal — narrate the setback and weave in \
the given `cost`.
  - CRIT_FAILURE: a disastrous failure — narrate it going badly wrong and weave in the \
given (heavier) `cost`.
- If a `cost` or `boon` is provided, its TYPE and SEVERITY/MAGNITUDE come from the engine \
— honour them. You may colour it with sensory detail, but you may not switch its category \
or add a different one.
- Keep it to 1-3 sentences. Be evocative but tight. Address the table naturally.
- Do not mention dice, DCs, or modifiers explicitly unless it adds flavor; describe the \
fiction, not the math."""


def _event_line(e, *, full: bool = True) -> str:
    """Format one past event as `- <actor> → <target>: <summary>`. When `full`, also feed
    back the PROSE that was narrated for it — that is what lets the narrator stay
    consistent with established fiction. Older events pass full=False to keep only the
    mechanical summary, so recent beats stay vivid without the window blowing up."""
    actor = e.actor_name
    target = (e.data or {}).get("target_name") if hasattr(e, "data") else None
    head = f"{actor} → {target}" if target else actor
    line = f"- {head}: {e.summary}"
    if full:
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
        present_lines = []
        for e in present:
            line = _entity_label(e)
            # NPC agenda is GM-only steering — surface it to the narrator so motives drive
            # behaviour, but flag it as not-to-be-revealed outright.
            agenda = (e.get("flags") or {}).get("agenda") if isinstance(e.get("flags"), dict) else None
            if agenda:
                line += f"\n  （暗中目標，僅供你鋪陳、勿直接揭露：{truncate(agenda, 80)}）"
            # Durable promises / standing facts this NPC made — re-injected every turn so
            # the narrator stays consistent with them no matter how long ago they happened.
            commitments = store.entity_commitments(e)
            if commitments:
                line += "\n  （已立下、必須遵守的承諾／既定事實：" + \
                    "；".join(truncate(c, 60) for c in commitments) + "）"
            present_lines.append(line)
        lines.append("HERE NOW:\n" + "\n".join(present_lines))
    if absent:
        gone = "、".join(e["name"] for e in absent)
        lines.append(f"NO LONGER PRESENT (do NOT bring them back into this location): {gone}")
    return "\n".join(lines)


def _quests_block(state: "GameState") -> str:
    try:
        quests = store.list_quests(scene_id=state.current_location_id)
    except Exception:  # noqa: BLE001
        return ""
    if not quests:
        return ""
    lines = []
    for q in quests[:8]:
        seed = q.get("seed") or {}
        details = q.get("details") or {}
        title = details.get("title") or seed.get("title_hint") or q.get("dedupe_key")
        objective = details.get("objective") or seed.get("objective_hint") or seed.get("premise") or ""
        lines.append(
            f"- {title} [{q.get('status')}/{q.get('detail_state')}], "
            f"giver={q.get('giver') or seed.get('giver')}, objective={truncate(objective, 120)}"
        )
    return "STABLE QUEST TRUTH (do not contradict):\n" + "\n".join(lines)


def narrate_context(state: "GameState", result: "ResolutionResult") -> str:
    # Layered memory: the most recent `full_n` beats keep their full prose; older beats
    # back to `window` keep only their mechanical summary line; anything older than that
    # lives in the rolling scene digest (compose_scene_summary). The very last event is
    # the one being narrated right now, so it is excluded.
    window = settings.narrate_context_window
    full_n = min(settings.narrate_full_context_window, window)
    prior = state.event_log[:-1] if len(state.event_log) > 1 else []
    recent_full = prior[-full_n:] if full_n else []
    older = prior[-window:-full_n] if window > full_n else []
    lines = [_event_line(e, full=False) for e in older]
    lines += [_event_line(e, full=True) for e in recent_full]
    history = "\n".join(lines) or "- (location just entered)"
    entities = _entities_block(state)
    parts = [
        f"LOCATION: {current_location_label(state)}\n{compose_scene_summary(state)}",
        f"現在時段：{state.time_of_day()}（以此為準；場景描述若暗示其他時間，一律以此時段為準）",
    ]
    card_block = _location_card_block(state)
    if card_block:
        parts.append(card_block)
    if entities:
        parts.append(entities)
    quests = _quests_block(state)
    if quests:
        parts.append(quests)
    parts += [
        f"RECENT EVENTS:\n{history}",
        "STRUCTURED RESULT (do not alter any of this):",
        # Localize the actor to the canonical Chinese name. Fed the English name, the
        # narrator invents its own (inconsistent) transliteration each turn (布蘭姆 vs
        # canonical 布拉姆), which then leaks into extraction and defeats PC de-dup. A
        # stable name keeps narration, extraction, and the PC guard aligned.
        f"  actor: {i18n.name(result.actor_name)}",
    ]
    # Critical: without target the model can't tell who the action is aimed at and
    # picks a random NPC from the scene description.
    if result.target_name:
        parts.append(f"  target: {i18n.name(result.target_name)}")
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
        parts.append(
            f"  band: {result.band.value} (CRIT_SUCCESS=exceptional success +extra boon, "
            "SUCCESS=clean success, FAILURE=did not achieve +a cost, "
            "CRIT_FAILURE=disastrous failure +a heavy cost)"
        )
    if result.cost:
        parts.append(
            f"  cost: type={result.cost.type.value}, severity={result.cost.severity.value}"
            + (f" — {result.cost.note}" if result.cost.note else "")
        )
    if result.boon:
        parts.append(
            f"  boon: type={result.boon.type.value}, magnitude={result.boon.magnitude.value}"
            + (f" — {result.boon.note}" if result.boon.note else "")
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
in a Pathfinder 2e session. There was NO dice roll — this action simply happens. \
Describe the moment: what the actor does and its immediate, mundane result.

ABSOLUTE RULES:
- Write ONLY in Traditional Chinese.
- Nothing is at stake: do NOT introduce success/failure, danger, dice, DCs, damage, or \
any new mechanical outcome.
- Do NOT invent new enemies, loot, or consequences; stay within the established scene.
- Keep it to 1-2 sentences. Be evocative but tight."""


# ───────────────────────── Location opener ─────────────────────────
NARRATE_QUEST_SYSTEM = f"""You are the GAME MASTER narrator for a Pathfinder 2e \
session, and you may also signal when an NPC is offering a quest.

Return ONLY JSON. `prose` is the Traditional Chinese GM narration. `quest_offer` is null \
unless an NPC is clearly issuing a task, commission, request for help, bounty, rescue, \
escort, investigation, or other playable mission in this very response.

Important:
- The quest_offer is only a compact seed for a background quest agent, not the full quest.
- Do not create quest_offer for ordinary colour, combat, environmental description, or a \
player merely thinking about a goal.
- If the player failed to obtain cooperation, keep quest_offer null.
- Use `direct_accept` when the NPC is openly offering the job.
- Use `requires_check` when the NPC is withholding the job until trust, credentials, \
negotiation, or proof is established.
- Tags must use the fixed eight-axis taxonomy names and values already implied by the JSON shape.

Respond with this exact shape:
{QUEST_SEED_JSON_SHAPE}"""


QUEST_AGENT_SYSTEM = f"""You are the QUEST AGENT for a living-world tabletop RPG.
You receive a compact quest seed plus brief context. Expand it into a stable executable \
quest card that future NPC narration must not contradict.

Rules:
- Write all player-facing text in Traditional Chinese.
- Do not invent unrelated plotlines; only stabilize and clarify what follows from the seed.
- Prefer concise concrete lists.
- The result should be useful once the player accepts the task: objective, known facts, \
details, next steps, success conditions, risks, reward, and fixed taxonomy tags.
- Tags must use the fixed eight-axis taxonomy only.

Respond with ONLY JSON:
{QUEST_DETAILS_JSON_SHAPE}"""


def quest_agent_context(state: "GameState", seed: dict) -> str:
    recent = state.event_log[-6:] if state.event_log else []
    history = "\n".join(_event_line(e) for e in recent) or "- (no recent events)"
    present = "\n".join(_entity_label(e) for e in state.present_entities()) or "- none"
    return (
        f"QUEST SEED JSON:\n{seed}\n\n"
        f"LOCATION: {current_location_label(state)}\n"
        f"SCENE SUMMARY:\n{compose_scene_summary(state)}\n\n"
        f"RELEVANT ENTITIES:\n{present}\n\n"
        f"RECENT EVENTS:\n{history}\n\n"
        "Expand this quest seed into the stable quest card now."
    )


SCENE_SYSTEM = """You are the GAME MASTER establishing a LOCATION in a Pathfinder 2e one-shot. \
Paint the place in 2-4 atmospheric sentences based on the provided summary. Do not resolve \
any actions or invent mechanics; just describe where the party finds themselves and invite \
them to act. The party arrives at this location naturally as the story unfolds — frame it \
as a place they have walked into, not a scripted scene.
Write ONLY in Traditional Chinese."""


def scene_context(state: "GameState") -> str:
    """Live context for the location opener (incl. the first /start description). Feeds the
    dynamic composed summary — the authored premise as a HIDDEN seed plus persistent changes
    and the entities actually present — instead of the static blurb, so the opening is
    generated fresh and stays consistent with real state."""
    return "\n".join([
        f"LOCATION: {current_location_label(state)}",
        compose_scene_summary(state),
        f"現在時段：{state.time_of_day()}（以此為準；場景描述若暗示其他時間，一律以此時段為準）",
        _location_card_block(state),
        "Establish this location now in Traditional Chinese:",
    ])


# ───────────────────────── Scene recap (/scene) ─────────────────────────
SCENE_RECAP_SYSTEM = """You are the GAME MASTER re-describing the situation AS IT CURRENTLY \
STANDS in a Pathfinder 2e session (the table asked to look around again). In 2-4 vivid sentences, \
present tense, describe where the party is now, who and what is present, and the current \
mood. Base it ONLY on the live state given (location, present/absent entities, persistent \
changes, time of day, recent events). Do NOT resolve actions, roll dice, invent new \
mechanics, or reintroduce anyone listed as no longer present. End by inviting the party to \
act. Write ONLY in Traditional Chinese."""


def scene_recap_context(state: "GameState") -> str:
    """Live context for /scene: the dynamic summary (base + persistent changes + present/
    absent entities), the current time, and a few recent beats — never the static blurb."""
    window = min(settings.narrate_context_window, 6)
    recent = state.event_log[-window:] if state.event_log else []
    history = "\n".join(_event_line(e) for e in recent) or "-（剛抵達此地，尚無事件）"
    return "\n".join([
        f"LOCATION: {current_location_label(state)}",
        compose_scene_summary(state),
        f"現在時段：{state.time_of_day()}（以此為準）",
        f"RECENT EVENTS:\n{history}",
        _location_card_block(state),
        "Describe the current situation now in Traditional Chinese:",
    ])


# ───────────────────────── Rolling scene digest ─────────────────────────
ROLLING_SUMMARY_SYSTEM = """You maintain the running MEMORY of a tabletop RPG session. \
You are given the PREVIOUS digest plus the latest events, and must return an UPDATED \
digest of the story so far for THIS location.

RULES:
- Write ONLY in Traditional Chinese, as a short bullet list (max 8 lines, one fact each).
- Keep DURABLE facts: decisions made, secrets revealed, deals struck, who did what to \
whom, unresolved threads, where the party intends to go. Drop momentary flavour.
- NEVER include dice, HP, damage, DCs, or numbers — only fiction.
- MERGE, don't append: fold new events into the existing lines, drop anything now \
obsolete or superseded, keep it tight. Prefer rewriting over growing.
- Do not restate the static backstory; record only what has HAPPENED since.
- Output ONLY the bullet lines, no preamble, no headings."""


def rolling_summary_context(state: "GameState") -> str:
    """Feed the previous digest + the recent beats so the model can refresh the running
    memory. Uses the full event window (the span about to scroll out of full-prose
    context), so nothing important is silently forgotten."""
    window = settings.narrate_context_window
    recent = state.event_log[-window:] if state.event_log else []
    history = "\n".join(_event_line(e, full=True) for e in recent) or "-（尚無事件）"
    prev = ""
    try:
        ss = store.get_scene_state(state.current_location_id)
        prev = ((ss or {}).get("current_summary") or "").strip()
        base = ((ss or {}).get("base_summary") or "").strip()
        if prev == base:
            prev = ""  # not yet diverged from backstory → treat as empty
    except Exception:  # noqa: BLE001
        prev = ""
    return "\n".join([
        f"LOCATION: {current_location_label(state)}",
        "PREVIOUS DIGEST:\n" + (prev or "-（尚無）"),
        f"NEW EVENTS:\n{history}",
        "Return the updated digest now (Traditional Chinese bullets only):",
    ])


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
clearly shifts. Use `disposition`:"attack" only when that NPC has clearly decided to \
start violence now and should pull the party into turn-based combat.
- Use `add_conditions` / `remove_conditions` ONLY when the narration shows the entity \
*newly* gained or *clearly lost* a mechanical state — e.g. tied up → add ["restrained"], \
the figure awoke → remove ["unconscious", "hypnotized"]. Pick ids from the allowed list \
in the JSON shape; unknown ids are silently dropped. Do NOT re-report conditions the \
entity already has.
- Set `register_kind`/`register_name` for anything genuinely NEW and not already listed: \
a person/creature/object that appears, OR a named place the narration introduces (use \
`register_kind`:"location"). Reporting a new place is just a candidate — the engine \
decides when it becomes permanent, so report it whenever it is clearly named.
- Set `commitment` ONLY when an NPC makes a PROMISE, reveals a standing fact, or shifts \
their lasting stance toward the party that must hold for the rest of the session — e.g. \
"答應帶路去地窖", "供出走私船叫海燕號", "從此視玩家為盟友". This is for durable \
relationship/plot memory, NOT for one-off mood or movement. Leave it null otherwise. The \
`entity_ref` must be the NPC who made it.
- Set `location_note` ONLY for a lasting change to the PLACE itself that is not tied to \
any single entity — e.g. a trap is disarmed, a fire is lit, water is spilled, a door is \
broken open. Leave it null for ordinary movement or for changes already captured by an \
entity delta above.

- Set `item_grants` ONLY when the narration clearly transfers possession: the actor \
picks up, receives, buys, loots, is handed, or is awarded an item from a plausible \
in-scene source. Mere mention, scenery, looking at an item, wanting an item, or unfinished \
negotiation MUST leave `item_grants` empty. Do not register loot just because it exists \
in the room. Use `recipient_ref` only when the narration names a recipient; otherwise \
leave it null so the engine can use the acting PC.

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
    pcs = "、".join(i18n.name(c.name) for c in state.pcs()) or "（無）"
    return (
        f"PLAYER CHARACTERS — these are the players' own characters. NEVER emit a delta "
        f"for them and NEVER register them as an entity (no register_kind/register_name "
        f"for these names): {pcs}\n\n"
        f"KNOWN ENTITIES:\n{lines}\n\n"
        f"GM NARRATION:\n{prose}\n\n"
        "Extract entity-state changes as JSON:"
    )
