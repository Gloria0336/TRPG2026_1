"""GameState: the single in-memory source of truth, with JSON snapshot + change feed.

Single session for the MVP (bound to one Discord channel). Both front-ends import the
module-level accessors (get_state / set_state) to share the same object in-process.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from ..config import settings
from ..content import monsters, scenario
from ..content.characters import premade_pcs
from ..db import store
from ..engine.combat import start_combat
from ..engine.types import Character, CombatState, Event, ResolutionResult
from ..logging_setup import get_logger

log = get_logger("state")


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
    # Ordered list of CostType string values that PARTIAL/FAILURE checks may draw
    # from in this scene (design §4.7). Empty list → engine falls back to a
    # skill-based default. See resolution.pick_cost.
    cost_pool: list[str] = field(default_factory=list)

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
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "summary": self.summary,
            "challenges": self.challenges, "advantage_skills": self.advantage_skills,
            "npcs": self.npcs, "onboarding": self.onboarding,
            "cost_pool": self.cost_pool,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        return cls(
            id=d["id"], title=d["title"], summary=d["summary"],
            challenges=d.get("challenges", {}), advantage_skills=d.get("advantage_skills", {}),
            npcs=d.get("npcs", []), onboarding=d.get("onboarding", []),
            cost_pool=d.get("cost_pool", []),
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
        # exact name, then first-name / contains match
        for c in self.characters.values():
            if c.name.lower() == rl:
                return c
        for c in self.characters.values():
            if rl in c.name.lower() or c.name.lower().split()[0] == rl:
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

    # ── scene / encounter orchestration ──
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
        # Pending freeplay actions are backed by Discord button callbacks, which
        # live only in memory. After a restart/load the button can no longer
        # complete, so keeping this flag would block new /action commands before
        # they reach the AI intent parser.
        gs.flags.pop("pending_freeplay_actor_id", None)
        gs.combat = CombatState.from_dict(d["combat"]) if d.get("combat") else None
        gs.started = d.get("started", False)
        gs.event_log = [Event.from_dict(e) for e in d.get("event_log", [])]
        gs.version = d.get("version", 0)
        return gs

    def save(self) -> None:
        settings.session_path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls) -> "GameState | None":
        path = settings.session_path
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
    # Start each campaign from a clean continuity store (entities/history/summaries).
    try:
        store.reset_world()
        store.seed_locations(scenario.LOCATIONS)
    except Exception as exc:  # noqa: BLE001
        log.warning("new_game: DB reset/seed failed (%s): %s", type(exc).__name__, exc)
    for pc in premade_pcs():
        gs.characters[pc.id] = pc
        gs.pc_ids.append(pc.id)
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
