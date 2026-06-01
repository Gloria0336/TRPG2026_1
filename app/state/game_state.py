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
from ..engine.combat import start_combat
from ..engine.types import Character, CombatState, Event, ResolutionResult


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
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "summary": self.summary,
            "challenges": self.challenges, "advantage_skills": self.advantage_skills,
            "npcs": self.npcs, "onboarding": self.onboarding,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Scene":
        return cls(
            id=d["id"], title=d["title"], summary=d["summary"],
            challenges=d.get("challenges", {}), advantage_skills=d.get("advantage_skills", {}),
            npcs=d.get("npcs", []), onboarding=d.get("onboarding", []),
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
    def goto_scene(self, scene_def: dict) -> None:
        # Remove leftover monsters from a previous scene.
        for cid in [c.id for c in self.characters.values() if not c.is_pc]:
            del self.characters[cid]
        self.combat = None
        self.scene = Scene.from_def(scene_def)
        self.flags["scene_id"] = scene_def["id"]
        self.bump()

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
        self.bump()
        return event

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


def has_active_campaign() -> bool:
    """Return True when a started campaign has not been marked over."""
    return bool(_current and _current.started and not _current.flags.get("over"))


def reset_state(channel_id: int | None = None) -> "GameState":
    gs = new_game(channel_id)
    set_state(gs)
    return gs
