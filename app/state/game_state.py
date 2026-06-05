"""GameState: the single in-memory source of truth, with JSON snapshot + change feed.

Single session for the MVP (bound to one Discord channel). Both front-ends import the
module-level accessors (get_state / set_state) to share the same object in-process.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from ..config import settings
from ..content import items as item_catalog
from ..content import monsters, scenario
from ..content.characters import premade_pcs
from ..db import store
from . import campaigns
from ..engine import guild_rank, progression
from ..engine.combat import start_combat
from ..engine.types import Character, CombatState, Event, ResolutionResult
from ..logging_setup import get_logger

log = get_logger("state")


def _dashboard_quest(q: dict) -> dict:
    seed = q.get("seed") or {}
    details = q.get("details") or {}
    accepted = q.get("status") in {"accepted", "completed", "failed", "expired"}
    title = details.get("title") or seed.get("title_hint") or q.get("dedupe_key")
    summary = seed.get("premise") or details.get("objective") or seed.get("objective_hint") or ""
    payload = {
        "id": q.get("id"),
        "dedupe_key": q.get("dedupe_key"),
        "scene_id": q.get("scene_id"),
        "giver": q.get("giver") or seed.get("giver") or details.get("giver") or "",
        "status": q.get("status"),
        "visibility": q.get("visibility"),
        "detail_state": q.get("detail_state"),
        "title": title,
        "summary": summary,
        "objective": details.get("objective") or seed.get("objective_hint") or "",
        "reward": details.get("reward") or seed.get("reward_hint") or "",
        "acceptance_mode": seed.get("acceptance_mode") or "direct_accept",
        "required_check": seed.get("required_check"),
        "tags": q.get("tags") or {},
    }
    if accepted:
        payload["details"] = {
            "known_info": details.get("known_info") or [],
            "details": details.get("details") or [],
            "next_steps": details.get("next_steps") or [],
            "success_conditions": details.get("success_conditions") or [],
            "failure_risks": details.get("failure_risks") or [],
        }
    return payload


# Ordered day stages (design: time is world state, not narrator whim). The canonical
# clock is absolute minutes in flags["world_minutes"]; stages are derived labels.
TIME_OF_DAY_STAGES = ["清晨", "上午", "下午", "傍晚", "夜晚"]
WORLD_START_MINUTES = 9 * 60


def _legacy_inventory_lines(line: str) -> list[str]:
    text = (line or "").strip()
    if not text:
        return []
    if text == "鏈甲與盾牌":
        return [part.strip() for part in text.split("與") if part.strip()]
    return [text]


def _migrate_character_inventory(c: Character) -> None:
    """Move legacy free-text inventory into the relational inventory tables."""
    try:
        store.seed_items(item_catalog.SEED_ITEMS)
        if not store.get_inventory(c.id):
            for raw in c.inventory:
                for line in _legacy_inventory_lines(raw):
                    item = item_catalog.parse_freetext(line)
                    if not item:
                        continue
                    store.grant_item(
                        c.id,
                        item["name"],
                        quantity=int(item.get("quantity") or 1),
                        category=item.get("category"),
                        slot=item.get("slot"),
                        aliases=item.get("aliases", []),
                        description=item.get("description", ""),
                        metadata=item.get("metadata", {}),
                        source=item.get("source", "dynamic"),
                        stackable=bool(item.get("stackable", True)),
                    )
        projected = store.project_inventory(c.id)
        if projected:
            c.inventory = projected
    except Exception as exc:  # noqa: BLE001
        log.warning("inventory migration failed for %s (%s): %s", c.id, type(exc).__name__, exc)


def _refresh_inventory_projections(characters: dict[str, Character]) -> None:
    for c in characters.values():
        _migrate_character_inventory(c)
STAGE_START_MINUTES = {
    "清晨": 5 * 60,
    "上午": 8 * 60,
    "下午": 12 * 60,
    "傍晚": 17 * 60,
    "夜晚": 20 * 60,
}
_COMBAT_ENTITY_KINDS = {"person", "creature"}
_HOSTILE_COMBAT_DISPOSITIONS = {"hostile", "attack"}
_HOSTILE_SINCE_FLAG = "hostile_since_round"

# Provocation ladder (design: attacking a peaceful unit shouldn't snap straight into
# combat — it degrades attitude one rung per swing). Attacking a unit already on the
# bottom rung (hostile/attack) is what actually starts combat, so:
#   friendly → tolerates 2 swings, fights on the 3rd
#   neutral  → tolerates 1 swing,  fights on the 2nd
#   hostile  → fights immediately
# `wary` collapses onto `neutral`; `afraid`/`cowed` never escalate (they flee/beg).
_PROVOKE_LADDER = ("friendly", "neutral", "hostile")
_PROVOKE_SUBMISSIVE_DISPOSITIONS = {"afraid", "cowed"}
_PROVOKE_BASELINE_FLAG = "provoke_baseline"   # attitude before the first provocation
_PROVOKED_AT_FLAG = "provoked_at"             # world_minutes of the latest provocation
_PROVOKE_RECOVER_MINUTES = 24 * 60            # a calm in-game day restores the baseline

_QUEST_REWARD_BY_RISK: dict[str, tuple[int, int]] = {
    "trivial": (1, 2),
    "low": (1, 5),
    "moderate": (2, 10),
    "high": (3, 25),
    "deadly": (4, 60),
    "unknown": (1, 5),
}


# ───────────────────────── Scene ─────────────────────────
@dataclass
class Scene:
    id: str
    title: str
    summary: str
    challenges: dict[str, int] = field(default_factory=dict)
    advantage_skills: dict[str, str] = field(default_factory=dict)
    npcs: list[str] = field(default_factory=list)
    onboarding: list[str] = field(default_factory=list)
    # Ordered list of CostType string values that FAILURE/CRIT_FAILURE checks may draw
    # from in this scene (design §4.7). Empty list → engine falls back to a
    # skill-based default. See resolution.pick_cost.
    cost_pool: list[str] = field(default_factory=list)
    # Ordered list of BoonType string values that a CRIT_SUCCESS may draw from in this
    # scene (design §4.4 大成功額外效果). Empty → skill-based default. See resolution.pick_boon.
    boon_pool: list[str] = field(default_factory=list)

    def advantage_for(self, skill: str) -> tuple[bool, bool]:
        tag = self.advantage_skills.get(skill, "")
        return (tag == "advantage", tag == "disadvantage")

    @classmethod
    def from_def(cls, d: dict) -> "Scene":
        return cls(
            id=d["id"],
            title=d["title"],
            summary=d["summary"],
            challenges=dict(d.get("challenges", {})),
            advantage_skills=dict(d.get("advantage", {})),
            npcs=list(d.get("npcs", [])),
            onboarding=list(d.get("onboarding", [])),
            cost_pool=list(d.get("cost_pool", [])),
            boon_pool=list(d.get("boon_pool", [])),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "summary": self.summary,
            "challenges": self.challenges, "advantage_skills": self.advantage_skills,
            "npcs": self.npcs, "onboarding": self.onboarding,
            "cost_pool": self.cost_pool, "boon_pool": self.boon_pool,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        return cls(
            id=d["id"], title=d["title"], summary=d["summary"],
            challenges=d.get("challenges", {}), advantage_skills=d.get("advantage_skills", {}),
            npcs=d.get("npcs", []), onboarding=d.get("onboarding", []),
            cost_pool=d.get("cost_pool", []), boon_pool=d.get("boon_pool", []),
        )


# ───────────────────────── GameState ─────────────────────────
class GameState:
    def __init__(self, channel_id: int | None = None):
        self.channel_id: int | None = channel_id
        self.characters: dict[str, Character] = {}
        self.pc_ids: list[str] = []
        # Discord user id (str) -> pc id. A player "claims" one of the pre-made PCs.
        self.players: dict[str, str] = {}
        # Discord user id (str) -> display name/nickname shown on the dashboard.
        self.player_names: dict[str, str] = {}
        self.scene: Scene = Scene(id="prelude", title="序幕", summary="冒險尚未開始。")
        # Where the party physically is (design §6: location is first-class state). Kept
        # in sync with scene.id by goto_scene/goto_location; the canonical scope key for
        # entity presence and the dynamic scene summary.
        self.party_location_id: str = self.scene.id
        self.flags: dict[str, object] = {}
        self.event_log: list[Event] = []
        self.combat: CombatState | None = None
        self.started: bool = False
        # SSE subscribers (asyncio queues). Populated by the web layer.
        self._subscribers: set[asyncio.Queue] = set()
        self.version: int = 0

    # ── character lookup ──
    def find_character(self, ref: str | None) -> Character | None:
        if not ref:
            return None
        if ref in self.characters:
            return self.characters[ref]
        rl = ref.strip().lower()
        from ..discord_bot import i18n

        # Exact internal/display name, then first-name / contains match.
        for c in self.characters.values():
            if c.name.lower() == rl or i18n.name(c.name).lower() == rl:
                return c
        for c in self.characters.values():
            names = (c.name.lower(), i18n.name(c.name).lower())
            if any(rl in n or n.split()[0] == rl for n in names if n):
                return c
        return None

    def pcs(self) -> list[Character]:
        return [self.characters[i] for i in self.pc_ids if i in self.characters]

    # ── player ↔ PC claims ──
    def claim_pc(self, user_id: str, pc_id: str, display_name: str | None = None) -> bool:
        """Assign a PC to a Discord user. Returns False if the PC is already taken."""
        if pc_id not in self.pc_ids:
            return False
        if pc_id in self.players.values() and self.players.get(user_id) != pc_id:
            return False
        self.players[user_id] = pc_id
        if display_name:
            self.player_names[user_id] = display_name
        order = list(self.flags.get("freeplay_turn_order", []))
        if pc_id not in order:
            order.append(pc_id)
            self.flags["freeplay_turn_order"] = order
        self.bump()
        return True

    def claim_for_pc(self, pc_id: str) -> dict | None:
        for user_id, claimed_pc_id in self.players.items():
            if claimed_pc_id == pc_id:
                return {
                    "user_id": user_id,
                    "display_name": self.player_names.get(user_id) or user_id,
                }
        return None

    def pc_for_user(self, user_id: str) -> Character | None:
        pid = self.players.get(user_id)
        return self.characters.get(pid) if pid else None

    def unclaimed_pcs(self) -> list[Character]:
        taken = set(self.players.values())
        return [self.characters[i] for i in self.pc_ids if i not in taken]

    # ── non-combat turn order ──
    def freeplay_turn_order(self) -> list[str]:
        claimed = set(self.players.values())
        saved = [pid for pid in self.flags.get("freeplay_turn_order", []) if pid in claimed]
        missing = [pid for pid in self.pc_ids if pid in claimed and pid not in saved]
        order = saved + missing
        if order != self.flags.get("freeplay_turn_order"):
            self.flags["freeplay_turn_order"] = order
        return order

    def current_freeplay_actor_id(self) -> str | None:
        order = self.freeplay_turn_order()
        if not order:
            return None
        index = int(self.flags.get("freeplay_turn_index", 0)) % len(order)
        return order[index]

    def pending_freeplay_actor_id(self) -> str | None:
        pending = self.flags.get("pending_freeplay_actor_id")
        return str(pending) if pending else None

    def begin_freeplay_action(self, actor_id: str) -> None:
        self.flags["pending_freeplay_actor_id"] = actor_id
        self.bump()

    def complete_freeplay_action(self, actor_id: str) -> str | None:
        self.flags.pop("pending_freeplay_actor_id", None)
        order = self.freeplay_turn_order()
        if not order:
            self.bump()
            return None
        current = self.current_freeplay_actor_id()
        if current == actor_id:
            index = (int(self.flags.get("freeplay_turn_index", 0)) + 1) % len(order)
            self.flags["freeplay_turn_index"] = index
            if index == 0:
                self.flags["freeplay_round"] = int(self.flags.get("freeplay_round", 1)) + 1
        self.bump()
        return self.current_freeplay_actor_id()

    def clear_pending_freeplay_action(self) -> None:
        if "pending_freeplay_actor_id" in self.flags:
            self.flags.pop("pending_freeplay_actor_id", None)
            self.bump()

    # ── clarification stack (per-actor C-tier follow-up history) ──
    # Each actor can have at most one open clarification thread at a time. We
    # store them as a dict keyed by actor_id so two PCs can converge in parallel.
    # Each turn = {"player": str, "gm": str}: the player's utterance that round
    # plus the GM follow-up that came after it. Capturing the player's words (not
    # just the GM's question) keeps the ORIGINAL goal in the history so the parser
    # merges the whole thread into one intent instead of anchoring on its own last
    # question — the "doesn't remember what the player just said" bug.
    # MAX_CLARIFICATION_TURNS bounds the loop — if we still haven't converged
    # the dispatcher gives up and narrates a no-roll beat so play moves on.
    MAX_CLARIFICATION_TURNS = 3

    def _clarification_store(self) -> dict:
        return self.flags.setdefault("clarifications", {})

    def get_clarification(self, actor_id: str) -> list[dict]:
        """The open follow-up history for this actor, oldest→newest. Empty when
        nothing is pending."""
        entry = self._clarification_store().get(actor_id)
        return list(entry.get("turns", [])) if entry else []

    def push_clarification(self, actor_id: str, player_text: str, gm_question: str) -> int:
        """Record one round on the actor's open thread: the player's utterance that
        triggered this round + the GM follow-up we're about to post. The triggering
        utterance is captured HERE (not on the next /action) so the player's words
        are never lost from the history. Returns the new total turn count (use to
        enforce MAX_CLARIFICATION_TURNS)."""
        store = self._clarification_store()
        entry = store.setdefault(actor_id, {"turns": []})
        entry["turns"].append({"player": player_text, "gm": gm_question})
        self.bump()
        return len(entry["turns"])

    def clear_clarification(self, actor_id: str) -> None:
        store = self._clarification_store()
        if actor_id in store:
            store.pop(actor_id, None)
            self.bump()

    def clarification_turn_count(self, actor_id: str) -> int:
        entry = self._clarification_store().get(actor_id)
        return len(entry.get("turns", [])) if entry else 0

    # ── scene / encounter orchestration ──
    # INVARIANT: party_location_id is engine-only state. It is written ONLY by
    # goto_scene / goto_location below. Narration and the entity extractor may READ it
    # and may move an individual NPC (entity.location_id, validated in store.apply_delta),
    # but must NEVER move the party — that is what keeps the fiction and the structured
    # state from diverging (the "teleport back" bug).
    @property
    def current_location_id(self) -> str:
        """Canonical scope key for entities / scene summary. Equals scene.id, but named
        for what it is: where the party currently is."""
        return self.party_location_id or self.scene.id

    def goto_scene(self, scene_def: dict) -> None:
        # Remove leftover monsters from a previous scene.
        for cid in [c.id for c in self.characters.values() if not c.is_pc]:
            del self.characters[cid]
        self.combat = None
        self.scene = Scene.from_def(scene_def)
        self.party_location_id = scene_def["id"]
        self.flags["scene_id"] = scene_def["id"]
        self._mark_visited(scene_def["id"])
        self._seed_scene_memory(scene_def)
        self.bump()

    # ── time of day (world state) ──
    def world_minutes(self) -> int:
        raw = self.flags.get("world_minutes")
        if isinstance(raw, (int, float)):
            minutes = int(raw)
        else:
            legacy = self.flags.get("time_of_day")
            if legacy in STAGE_START_MINUTES:
                minutes = WORLD_START_MINUTES if legacy == "上午" else STAGE_START_MINUTES[str(legacy)]
            else:
                minutes = WORLD_START_MINUTES
            self.flags["world_minutes"] = minutes
        self.flags["time_of_day"] = self._stage_for_minutes(minutes)
        return minutes

    @staticmethod
    def _stage_for_minutes(minutes: int) -> str:
        minute = int(minutes) % (24 * 60)
        if STAGE_START_MINUTES["清晨"] <= minute < STAGE_START_MINUTES["上午"]:
            return "清晨"
        if STAGE_START_MINUTES["上午"] <= minute < STAGE_START_MINUTES["下午"]:
            return "上午"
        if STAGE_START_MINUTES["下午"] <= minute < STAGE_START_MINUTES["傍晚"]:
            return "下午"
        if STAGE_START_MINUTES["傍晚"] <= minute < STAGE_START_MINUTES["夜晚"]:
            return "傍晚"
        return "夜晚"

    def time_of_day(self) -> str:
        return self._stage_for_minutes(self.world_minutes())

    def advance_minutes(self, mins: int | float) -> str:
        delta = max(0, int(round(float(mins))))
        if delta <= 0:
            return self.time_of_day()
        minutes = self.world_minutes() + delta
        self.flags["world_minutes"] = minutes
        self.flags["time_of_day"] = self._stage_for_minutes(minutes)
        self.bump()
        return str(self.flags["time_of_day"])

    def advance_time(self, steps: int = 1) -> str:
        """Advance by whole day-stage boundaries, preserving old callers."""
        step_count = max(int(steps), 0)
        if step_count <= 0:
            return self.time_of_day()
        minutes = self.world_minutes()
        minute_of_day = minutes % (24 * 60)
        current_stage = self._stage_for_minutes(minutes)
        current_index = TIME_OF_DAY_STAGES.index(current_stage)
        cycle_day = minutes // (24 * 60)
        if current_stage == "夜晚" and minute_of_day < STAGE_START_MINUTES["清晨"]:
            cycle_day -= 1
        total_index = current_index + step_count
        day_offset, target_index = divmod(total_index, len(TIME_OF_DAY_STAGES))
        target_stage = TIME_OF_DAY_STAGES[target_index]
        target_minutes = (cycle_day + day_offset) * (24 * 60) + STAGE_START_MINUTES[target_stage]
        self.flags["world_minutes"] = target_minutes
        self.flags["time_of_day"] = target_stage
        self.bump()
        return target_stage

    def _mark_visited(self, location_id: str) -> None:
        """Append to the party's visited-locations trail (drives the goal director)."""
        visited = self.flags.setdefault("visited_locations", [])
        if location_id not in visited:
            visited.append(location_id)

    def goto_location(self, location_id: str, *, title: str, summary: str = "") -> None:
        """Move the party to a free location (entity-backed, not a scripted scene). Unlike
        goto_scene this is NOT tied to scenario.scene_by_id — it powers natural-language
        travel so the structured world follows the fiction instead of drifting from it."""
        for cid in [c.id for c in self.characters.values() if not c.is_pc]:
            del self.characters[cid]
        self.combat = None
        self.scene = Scene(id=location_id, title=title, summary=summary)
        self.party_location_id = location_id
        self.flags["scene_id"] = location_id
        self._mark_visited(location_id)
        try:
            store.set_base_summary(location_id, summary)
        except Exception as exc:  # noqa: BLE001 — memory layer must never break play
            log.warning("goto_location: DB seed failed (%s): %s", type(exc).__name__, exc)
        self.bump()

    def _seed_scene_memory(self, scene_def: dict) -> None:
        """Register the scene's authored entities and base summary in the DB so the
        narrator reads structured presence/state instead of the static summary."""
        try:
            store.seed_entities(scene_def["id"], scene_def.get("entities", []))
            store.set_base_summary(scene_def["id"], scene_def.get("summary", ""))
        except Exception as exc:  # noqa: BLE001 — memory layer must never break play
            log.warning("goto_scene: DB seed failed (%s): %s", type(exc).__name__, exc)

    # ── narrative entities (DB-backed) ──
    def present_entities(self) -> list[dict]:
        try:
            return store.get_present(self.current_location_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("present_entities: DB read failed: %s", exc)
            return []

    def all_entities(self) -> list[dict]:
        try:
            return store.get_all(self.current_location_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("all_entities: DB read failed: %s", exc)
            return []

    @staticmethod
    def _can_fight_as_entity(ent: dict) -> bool:
        return ent.get("kind") in _COMBAT_ENTITY_KINDS

    def _write_entity_state(
        self,
        ent: dict,
        *,
        status: str | None = None,
        disposition: str | None = None,
        flags: dict | None = None,
    ) -> None:
        store.upsert_entity(
            id=ent["id"], scene_id=ent["scene_id"], kind=ent["kind"], name=ent["name"],
            aliases=ent.get("aliases", []),
            status=status if status is not None else ent["status"],
            location_id=ent.get("location_id"),
            disposition=disposition if disposition is not None else ent.get("disposition"),
            flags=flags if flags is not None else ent.get("flags", {}),
            notes=ent.get("notes", ""),
            first_seen_event_id=ent.get("first_seen_event_id"),
        )

    def start_scene_combat(self) -> CombatState | None:
        """Spawn the current scene's encounter monsters and roll initiative."""
        scene_def = scenario.scene_by_id(self.scene.id)
        if not scene_def or not scene_def.get("encounter"):
            return None
        spawned: list[str] = []
        for key, count in scene_def["encounter"]:
            for i in range(1, count + 1):
                mon = monsters.spawn(key, i)
                self.characters[mon.id] = mon
                spawned.append(mon.id)
        participant_ids = self.pc_ids + spawned
        combat = start_combat(self, participant_ids)
        self.bump()
        return combat

    def start_entity_combat(self, trigger_entity_id: str | None = None) -> CombatState | None:
        """Spawn hostile/attacking present entities as temporary combatants."""
        enemies: list[dict] = []
        seen: set[str] = set()
        for ent in self.present_entities():
            ent_id = ent.get("id")
            if not ent_id or ent_id in seen or not self._can_fight_as_entity(ent):
                continue
            should_join = ent.get("disposition") in _HOSTILE_COMBAT_DISPOSITIONS
            if trigger_entity_id and ent_id == trigger_entity_id:
                should_join = True
                if ent.get("disposition") not in _HOSTILE_COMBAT_DISPOSITIONS:
                    flags = dict(ent.get("flags") or {})
                    flags.pop(_HOSTILE_SINCE_FLAG, None)
                    self._write_entity_state(ent, disposition="hostile", flags=flags)
                    ent = {**ent, "disposition": "hostile", "flags": flags}
            if should_join:
                enemies.append(ent)
                seen.add(ent_id)

        if not enemies:
            return None

        spawned: list[str] = []
        for ent in enemies:
            mon = monsters.spawn_from_entity(ent)
            self.characters[mon.id] = mon
            spawned.append(mon.id)
        participant_ids = [pid for pid in self.pc_ids if pid in self.characters] + spawned
        if not spawned or not participant_ids:
            return None
        combat = start_combat(self, participant_ids)
        self.bump()
        return combat

    def escalate_hostiles(self) -> list[str]:
        """Promote NPCs from sustained hostile posture into active attack state."""
        try:
            round_no = int(self.flags.get("freeplay_round", 1))
        except (TypeError, ValueError):
            round_no = 1
        flipped: list[str] = []
        changed = False

        for ent in self.present_entities():
            if not self._can_fight_as_entity(ent):
                continue
            flags = dict(ent.get("flags") or {})
            if ent.get("disposition") == "hostile":
                try:
                    since = int(flags.get(_HOSTILE_SINCE_FLAG))
                except (TypeError, ValueError):
                    since = round_no
                    flags[_HOSTILE_SINCE_FLAG] = since
                    store.merge_entity_flags(ent["id"], {_HOSTILE_SINCE_FLAG: since})
                    changed = True
                    continue
                if round_no - since >= 3:
                    flags.pop(_HOSTILE_SINCE_FLAG, None)
                    self._write_entity_state(ent, disposition="attack", flags=flags)
                    flipped.append(ent["id"])
                    changed = True
            elif _HOSTILE_SINCE_FLAG in flags:
                store.merge_entity_flags(ent["id"], remove=[_HOSTILE_SINCE_FLAG])
                changed = True

        if changed:
            self.bump()
        return flipped

    # ── provocation ladder (player attacks a non-hostile unit) ──
    @staticmethod
    def is_combat_hostile(disposition: str | None) -> bool:
        """True when attacking this unit starts combat outright (bottom of the ladder)."""
        return disposition in _HOSTILE_COMBAT_DISPOSITIONS

    @staticmethod
    def _step_down_disposition(disposition: str | None) -> str:
        """One rung down the provocation ladder toward hostility. `wary` collapses onto
        `neutral`; anything already at/below `hostile` stays `hostile`."""
        cur = "neutral" if disposition == "wary" else (disposition or "neutral")
        if cur not in _PROVOKE_LADDER:
            return "hostile"
        idx = _PROVOKE_LADDER.index(cur)
        return _PROVOKE_LADDER[min(idx + 1, len(_PROVOKE_LADDER) - 1)]

    def provoke_entity(self, ent_id: str) -> str | None:
        """Record one player provocation (an attack that did NOT yet start combat): step the
        entity's attitude one rung down the ladder and stamp it for later recovery. Returns
        the NEW disposition, or None if the entity is gone. Submissive units (afraid/cowed)
        do not escalate — they keep their attitude and the caller narrates a flee/beg beat."""
        ent = store.get_entity_by_id(ent_id)
        if ent is None:
            return None
        cur = ent.get("disposition") or "neutral"
        if cur in _PROVOKE_SUBMISSIVE_DISPOSITIONS:
            return cur
        flags = dict(ent.get("flags") or {})
        flags.setdefault(_PROVOKE_BASELINE_FLAG, cur)   # remember pre-provocation attitude once
        flags[_PROVOKED_AT_FLAG] = self.world_minutes()
        new = self._step_down_disposition(cur)
        self._write_entity_state(ent, disposition=new, flags=flags)
        self.bump()
        return new

    def recover_provoked_dispositions(self) -> list[str]:
        """Restore baseline attitude for units that were provoked but left alone for a full
        in-game day (design D: provocation resets over time). Returns the restored ids."""
        now = self.world_minutes()
        restored: list[str] = []
        changed = False
        for ent in self.present_entities():
            flags = dict(ent.get("flags") or {})
            if _PROVOKED_AT_FLAG not in flags:
                continue
            try:
                since = int(flags[_PROVOKED_AT_FLAG])
            except (TypeError, ValueError):
                since = now
            if now - since < _PROVOKE_RECOVER_MINUTES:
                continue
            baseline = flags.pop(_PROVOKE_BASELINE_FLAG, None)
            flags.pop(_PROVOKED_AT_FLAG, None)
            self._write_entity_state(
                ent,
                disposition=baseline if baseline in store.DISPOSITIONS else ent.get("disposition"),
                flags=flags,
            )
            restored.append(ent["id"])
            changed = True
        if changed:
            self.bump()
        return restored

    def reconcile_combat_entities(self) -> list[str]:
        """Write entity-backed combat outcomes back to the durable world state."""
        if not self.combat:
            return []
        touched: list[str] = []
        outcome = self.combat.outcome
        for cid, _init in self.combat.order:
            ch = self.characters.get(cid)
            if ch is None or ch.is_pc:
                continue
            ent = store.get_entity_by_id(cid)
            if ent is None:
                continue
            flags = dict(ent.get("flags") or {})
            flags.pop(_HOSTILE_SINCE_FLAG, None)
            if ch.is_dead or ch.is_down:
                self._write_entity_state(ent, status="dead", flags=flags)
            elif outcome == "victory":
                self._write_entity_state(ent, disposition="cowed", flags=flags)
            else:
                self._write_entity_state(ent, flags=flags)
            touched.append(cid)
        if touched:
            self.bump()
        return touched

    # ── event log ──
    def add_event(self, event: Event) -> Event:
        self.event_log.append(event)
        self._mirror_event(event)
        self.bump()
        return event

    def _mirror_event(self, event: Event) -> None:
        """Persist the event to the DB event_log (durable history, incl. prose)."""
        try:
            store.insert_event(
                id=event.id, scene_id=self.scene.id, actor_id=event.actor_id,
                actor_name=event.actor_name, kind=event.kind, summary=event.summary,
                narration=event.narration, scope=event.scope, data=event.data, ts=event.ts,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("add_event: DB mirror failed (%s): %s", type(exc).__name__, exc)

    def add_system_event(self, kind: str, summary: str, narration: str = "") -> Event:
        return self.add_event(Event(actor_id="system", actor_name="GM", kind=kind, summary=summary, narration=narration))

    def log_result(self, result: ResolutionResult, narration: str = "") -> Event:
        return self.add_event(
            Event(
                actor_id=result.actor_id,
                actor_name=result.actor_name,
                kind=result.kind.value,
                summary=result.summary,
                narration=narration,
                data=result.to_dict(),
            )
        )

    def complete_quest(self, quest_id: str, *, actor_id: str | None = None) -> dict | None:
        """Mark a quest completed and grant default SP/merit rewards to PCs.

        Reward amounts are intentionally small, bounded defaults keyed by risk_level;
        authored quest tags may override them with integer `reward_sp` / `reward_merit`.
        """
        quest = store.update_quest_status(quest_id, "completed")
        if quest is None:
            return None
        tags = quest.get("tags") or {}
        risk = str(tags.get("risk_level") or "unknown")
        default_sp, default_merit = _QUEST_REWARD_BY_RISK.get(risk, _QUEST_REWARD_BY_RISK["unknown"])
        try:
            sp = max(0, int(tags.get("reward_sp", default_sp)))
        except (TypeError, ValueError):
            sp = default_sp
        try:
            merit = max(0, int(tags.get("reward_merit", default_merit)))
        except (TypeError, ValueError):
            merit = default_merit

        awarded: list[str] = []
        for pc in self.pcs():
            progression.grant_skill_points(pc, sp)
            guild_rank.award_merit(pc, merit)
            awarded.append(f"{pc.name} +{sp} SP / +{merit} merit")
        title = (quest.get("details") or {}).get("title") or (quest.get("seed") or {}).get("title_hint") or quest_id
        self.add_event(Event(
            actor_id=actor_id or "system",
            actor_name=self.characters.get(actor_id).name if actor_id and actor_id in self.characters else "GM",
            kind="quest",
            summary=f"任務完成：{title}（" + "；".join(awarded) + "）",
            data={"quest_id": quest_id, "reward_sp": sp, "reward_merit": merit},
        ))
        self.bump()
        return quest

    def fail_quest(self, quest_id: str, *, actor_id: str | None = None) -> dict | None:
        quest = store.update_quest_status(quest_id, "failed")
        if quest is None:
            return None
        for pc in self.pcs():
            pc.standing -= 1
        self.add_event(Event(
            actor_id=actor_id or "system",
            actor_name=self.characters.get(actor_id).name if actor_id and actor_id in self.characters else "GM",
            kind="quest",
            summary=f"任務失敗：{quest_id}（公會聲望 -1）",
            data={"quest_id": quest_id},
        ))
        self.bump()
        return quest

    def set_narration(self, event_id: str, narration: str) -> None:
        """Attach AI prose to a previously-logged event (narration is async, §5.4)."""
        for ev in reversed(self.event_log):
            if ev.id == event_id:
                ev.narration = narration
                try:
                    store.update_narration(event_id, narration)
                except Exception as exc:  # noqa: BLE001
                    log.warning("set_narration: DB update failed: %s", exc)
                self.bump()
                return

    # ── change feed (SSE) ──
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def bump(self) -> None:
        """Signal subscribers that state changed. Safe to call from the event loop."""
        self.version += 1
        for q in list(self._subscribers):
            try:
                q.put_nowait(self.version)
            except asyncio.QueueFull:
                pass

    # ── snapshot for the dashboard (read-only view) ──
    def dashboard_view(self) -> dict:
        combat = None
        if self.combat:
            order = []
            for cid, init in self.combat.order:
                c = self.characters.get(cid)
                if c:
                    order.append({"id": cid, "name": c.name, "init": init, "is_pc": c.is_pc})
            combat = {
                "active": self.combat.active,
                "round": self.combat.round,
                "current_id": self.combat.current_id,
                "outcome": self.combat.outcome,
                "order": order,
            }
        return {
            "version": self.version,
            "started": self.started,
            "scene": self.scene.to_dict(),
            "characters": [
                {
                    "id": c.id, "name": c.name, "is_pc": c.is_pc, "portrait": c.portrait,
                    "level": c.level, "hp": c.hp, "max_hp": c.max_hp, "ac": c.ac,
                    "conditions": c.conditions, "blurb": c.blurb,
                    "abilities": c.abilities,
                    "skill_prof": c.skill_prof,
                    "skill_points": c.skill_points,
                    "lore_prof": c.lore_prof,
                    "guild_rank": c.guild_rank,
                    "merit": c.merit,
                    "standing": c.standing,
                    "rank_flags": c.rank_flags,
                    "actions": [a.name for a in c.actions],
                    "claim": self.claim_for_pc(c.id) if c.is_pc else None,
                }
                for c in self.characters.values()
            ],
            "players": [
                {
                    "user_id": user_id,
                    "pc_id": pc_id,
                    "display_name": self.player_names.get(user_id) or user_id,
                }
                for user_id, pc_id in self.players.items()
            ],
            "combat": combat,
                "log": [
                {
                    "id": e.id, "actor": e.actor_name, "kind": e.kind,
                    "summary": e.summary, "narration": e.narration, "ts": e.ts,
                }
                for e in self.event_log[-60:]
            ],
            "quests": [_dashboard_quest(q) for q in store.list_quests()],
        }

    # ── JSON snapshot (no DB) ──
    def to_dict(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "pc_ids": self.pc_ids,
            "players": self.players,
            "player_names": self.player_names,
            "characters": {cid: c.to_dict() for cid, c in self.characters.items()},
            "scene": self.scene.to_dict(),
            "party_location_id": self.party_location_id,
            "flags": self.flags,
            "combat": self.combat.to_dict() if self.combat else None,
            "started": self.started,
            "event_log": [e.to_dict() for e in self.event_log],
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GameState":
        gs = cls(channel_id=d.get("channel_id"))
        gs.pc_ids = d.get("pc_ids", [])
        gs.players = d.get("players", {})
        gs.player_names = d.get("player_names", {})
        gs.characters = {cid: Character.from_dict(c) for cid, c in d.get("characters", {}).items()}
        gs.scene = Scene.from_dict(d["scene"]) if d.get("scene") else gs.scene
        gs.party_location_id = d.get("party_location_id") or gs.scene.id
        gs.flags = d.get("flags", {})
        gs.world_minutes()
        # Pending freeplay actions are backed by Discord button callbacks, which
        # live only in memory. After a restart/load the button can no longer
        # complete, so keeping this flag would block new /action commands before
        # they reach the AI intent parser.
        gs.flags.pop("pending_freeplay_actor_id", None)
        gs.combat = CombatState.from_dict(d["combat"]) if d.get("combat") else None
        gs.started = d.get("started", False)
        gs.event_log = [Event.from_dict(e) for e in d.get("event_log", [])]
        gs.version = d.get("version", 0)
        _refresh_inventory_projections(gs.characters)
        return gs

    def save(self) -> None:
        path = campaigns.active_session_path() or settings.session_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "GameState | None":
        path = campaigns.active_session_path() or settings.session_path
        if not path.exists():
            return None
        try:
            return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError):
            return None


# ───────────────────────── New-game builder ─────────────────────────
def new_game(channel_id: int | None = None) -> "GameState":
    """Fresh session: load the two pre-made PCs and the opening scene."""
    gs = GameState(channel_id=channel_id)
    # Each campaign gets its OWN fresh, empty continuity store (案 A): begin_new opens a
    # new per-campaign world.db so runtime data never pollutes the authored starter
    # content. Seeding then loads the starter locations/entities into that clean DB.
    try:
        campaigns.begin_new(channel_id, title=scenario.TITLE)
        store.seed_locations(scenario.LOCATIONS)
        store.seed_items(item_catalog.SEED_ITEMS)
    except Exception as exc:  # noqa: BLE001
        log.warning("new_game: campaign begin/seed failed (%s): %s", type(exc).__name__, exc)
    for pc in premade_pcs():
        gs.characters[pc.id] = pc
        gs.pc_ids.append(pc.id)
    _refresh_inventory_projections(gs.characters)
    gs.flags["world_minutes"] = WORLD_START_MINUTES
    gs.flags["time_of_day"] = TIME_OF_DAY_STAGES[1]  # campaign opens mid-morning
    gs.goto_scene(scenario.first_scene())
    gs.started = True
    gs.add_system_event("scene", f"新的冒險開始：{scenario.TITLE}")
    return gs


# ───────────────────────── Module-level shared session ─────────────────────────
_current: "GameState | None" = None


def get_state() -> "GameState | None":
    return _current


def set_state(state: "GameState | None") -> None:
    global _current
    _current = state


def active_campaign() -> "GameState | None":
    """Return the active campaign, even if its Discord channel binding is stale."""
    if _current and _current.started and not _current.flags.get("over"):
        return _current
    return None


def has_active_campaign() -> bool:
    """Return True when a started campaign has not been marked over."""
    return active_campaign() is not None


def has_discord_channel_binding(state: "GameState | None") -> bool:
    """Discord channel snowflakes are positive ints; 0/None means an unbound snapshot."""
    return isinstance(getattr(state, "channel_id", None), int) and state.channel_id > 0


def active_campaign_for_channel(channel_id: int) -> "GameState | None":
    gs = active_campaign()
    if gs and gs.channel_id == channel_id:
        return gs
    return None


def reset_state(channel_id: int | None = None) -> "GameState":
    gs = new_game(channel_id)
    set_state(gs)
    return gs
