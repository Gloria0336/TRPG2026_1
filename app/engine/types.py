"""Core data types for the Resolution Engine.

These are plain dataclasses with to_dict/from_dict so the whole game state can be
snapshotted to JSON (no database — design MVP constraint). Enums serialise as their
string values.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum


# ───────────────────────── Abilities & skills (5e) ─────────────────────────
class Ability(str, Enum):
    STR = "STR"
    DEX = "DEX"
    CON = "CON"
    INT = "INT"
    WIS = "WIS"
    CHA = "CHA"


# 5e skill → governing ability.
SKILLS: dict[str, Ability] = {
    "acrobatics": Ability.DEX,
    "animal_handling": Ability.WIS,
    "arcana": Ability.INT,
    "athletics": Ability.STR,
    "deception": Ability.CHA,
    "history": Ability.INT,
    "insight": Ability.WIS,
    "intimidation": Ability.CHA,
    "investigation": Ability.INT,
    "medicine": Ability.WIS,
    "nature": Ability.INT,
    "perception": Ability.WIS,
    "performance": Ability.CHA,
    "persuasion": Ability.CHA,
    "religion": Ability.INT,
    "sleight_of_hand": Ability.DEX,
    "stealth": Ability.DEX,
    "survival": Ability.WIS,
}


def ability_modifier(score: int) -> int:
    """5e ability modifier: floor((score - 10) / 2)."""
    return (score - 10) // 2


# Proficiency bonus by character level (5e table, levels 1-20).
def proficiency_bonus(level: int) -> int:
    return 2 + (max(1, min(level, 20)) - 1) // 4


# ───────────────────────── Damage / actions ─────────────────────────
@dataclass
class Damage:
    count: int
    sides: int
    bonus: int = 0
    type: str = "bludgeoning"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Damage":
        return cls(**d)


class ActionType(str, Enum):
    MELEE_ATTACK = "melee_attack"   # to-hit vs AC, then damage
    RANGED_ATTACK = "ranged_attack"
    SAVE_SPELL = "save_spell"        # target rolls a save; damage (maybe half) on save
    AUTO_HIT = "auto_hit"            # always hits (e.g. Magic Missile)
    HEAL = "heal"                    # restores hp
    UTILITY = "utility"              # narrative effect, no attack/damage


class ActionCost(str, Enum):
    ACTION = "action"
    BONUS = "bonus"
    FREE = "free"


@dataclass
class Action:
    """A thing a combatant can do on its turn (a weapon attack or a spell)."""

    name: str
    type: ActionType
    cost: ActionCost = ActionCost.ACTION
    to_hit: int = 0                       # attack bonus for *_ATTACK
    damage: list[Damage] = field(default_factory=list)
    save_ability: Ability | None = None   # for SAVE_SPELL
    save_dc: int | None = None
    save_effect: str = "half"             # "half" | "none" damage on a successful save
    heal: Damage | None = None            # for HEAL (sides as healing dice)
    range_ft: int = 5
    uses: int | None = None               # None = at-will; else remaining uses
    description: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["type"] = self.type.value
        d["cost"] = self.cost.value
        d["save_ability"] = self.save_ability.value if self.save_ability else None
        d["damage"] = [dm if isinstance(dm, dict) else dm.__dict__ for dm in self.damage] if self.damage else []
        d["heal"] = self.heal.__dict__ if self.heal else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Action":
        d = dict(d)
        d["type"] = ActionType(d["type"])
        d["cost"] = ActionCost(d.get("cost", "action"))
        d["save_ability"] = Ability(d["save_ability"]) if d.get("save_ability") else None
        d["damage"] = [Damage.from_dict(x) for x in d.get("damage", [])]
        d["heal"] = Damage.from_dict(d["heal"]) if d.get("heal") else None
        return cls(**d)


# ───────────────────────── Character (PC + NPC unified) ─────────────────────────
@dataclass
class Character:
    """A PC or NPC/monster. Design §6.0: PCs and NPCs share one entity table."""

    id: str
    name: str
    is_pc: bool
    level: int
    abilities: dict[str, int]                 # {"STR": 16, ...}
    max_hp: int
    hp: int
    ac: int
    speed: int = 30
    # skill name -> "prof" | "expertise"  (absent = not proficient)
    skill_prof: dict[str, str] = field(default_factory=dict)
    save_prof: list[str] = field(default_factory=list)  # proficient saving-throw abilities
    actions: list[Action] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    # 5e death saving throws (PCs only): successes/failures, and a "stable"/"dead" marker
    death_successes: int = 0
    death_failures: int = 0
    portrait: str = "🧝"                        # emoji shown in embeds/dashboard
    blurb: str = ""                             # one-line flavour for onboarding

    # ── derived helpers ──
    def mod(self, ability: Ability | str) -> int:
        key = ability.value if isinstance(ability, Ability) else ability
        return ability_modifier(self.abilities.get(key, 10))

    @property
    def prof_bonus(self) -> int:
        return proficiency_bonus(self.level)

    @property
    def is_down(self) -> bool:
        return self.hp <= 0

    @property
    def is_dead(self) -> bool:
        return "dead" in self.conditions

    def skill_bonus(self, skill: str) -> int:
        ability = SKILLS[skill]
        bonus = self.mod(ability)
        prof = self.skill_prof.get(skill)
        if prof == "prof":
            bonus += self.prof_bonus
        elif prof == "expertise":
            bonus += self.prof_bonus * 2
        return bonus

    def save_bonus(self, ability: Ability | str) -> int:
        key = ability.value if isinstance(ability, Ability) else ability
        bonus = self.mod(key)
        if key in self.save_prof:
            bonus += self.prof_bonus
        return bonus

    def find_action(self, name: str) -> Action | None:
        name_l = name.lower()
        for a in self.actions:
            if a.name.lower() == name_l:
                return a
        # loose contains-match so "sword" finds "Longsword"
        for a in self.actions:
            if name_l in a.name.lower() or a.name.lower() in name_l:
                return a
        return None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["actions"] = [a.to_dict() for a in self.actions]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Character":
        d = dict(d)
        d["actions"] = [Action.from_dict(a) for a in d.get("actions", [])]
        return cls(**d)

    def clone(self, *, id: str | None = None, name: str | None = None) -> "Character":
        """Deep-ish copy (used to spawn multiple monsters from one template)."""
        return Character.from_dict({**self.to_dict(), "id": id or self.id, "name": name or self.name})


# ───────────────────────── Intent (AI output) ─────────────────────────
class IntentTier(str, Enum):
    A = "A"   # clear action/target/approach → resolve directly
    B = "B"   # clear target, approach missing → offer candidate methods
    C = "C"   # intent unclear → ask a clarifying question


@dataclass
class Intent:
    """Structured intent produced by the AI intent-parser (design §8.3).

    The AI fills ONLY these slots — it never asserts success, DC, or facts.
    """

    actor_id: str
    raw_text: str
    tier: IntentTier
    action: str | None = None          # e.g. "pick_lock", "persuade", "attack"
    target: str | None = None          # free-text target reference
    approach: str | None = None        # skill / action name driving the check
    is_attack: bool = False            # True if the player is attempting to attack/fight
    candidates: list[str] = field(default_factory=list)  # tier B: candidate approaches
    question: str | None = None        # tier C: clarifying question
    options: list[str] = field(default_factory=list)     # tier C: option labels

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d


# ───────────────────────── ResolutionResult (engine output) ─────────────────────────
class ResultKind(str, Enum):
    CHECK = "check"
    ATTACK = "attack"
    SAVE = "save"
    HEAL = "heal"
    NARRATIVE = "narrative"   # no roll; pure scene/utility beat


@dataclass
class ResolutionResult:
    """Structured outcome computed entirely by the engine. The AI turns this into
    prose but must not change any number here (design §4.0 / §4.6)."""

    kind: ResultKind
    actor_id: str
    actor_name: str
    summary: str                       # terse mechanical summary, e.g. "Athletics check vs DC 15: SUCCESS"
    success: bool | None = None
    target_id: str | None = None
    target_name: str | None = None
    dc: int | None = None
    roll_breakdown: str | None = None  # human-readable dice breakdown for the embed
    natural: int | None = None
    crit: bool = False
    fumble: bool = False
    damage: int | None = None
    healing: int | None = None
    deltas: list[str] = field(default_factory=list)   # plain-language state changes applied
    # Hint to the narrator (style/tone), NOT a number it may alter.
    narration_hint: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        return d


# ───────────────────────── Combat state ─────────────────────────
@dataclass
class CombatState:
    """Turn-based combat tracker (design §10, simplified to synchronous 5e).

    `order` is a list of (combatant_id, initiative) sorted high→low. Positioning is
    abstracted (no grid); reactions/opportunity attacks are out of MVP scope.
    """

    order: list[tuple[str, int]] = field(default_factory=list)
    round: int = 1
    turn_index: int = 0
    action_used: bool = False
    bonus_used: bool = False
    active: bool = True
    outcome: str | None = None        # None while ongoing, else "victory"/"defeat"

    @property
    def current_id(self) -> str | None:
        if not self.order:
            return None
        return self.order[self.turn_index % len(self.order)][0]

    def to_dict(self) -> dict:
        d = asdict(self)
        # tuples become lists through asdict/JSON; keep as lists of [id, init].
        d["order"] = [list(x) for x in self.order]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "CombatState":
        d = dict(d)
        d["order"] = [tuple(x) for x in d.get("order", [])]
        return cls(**d)


# ───────────────────────── Event (append-only log) ─────────────────────────
@dataclass
class Event:
    """One entry in the append-only event_log (design §5.2 / §6).

    This is the single source of 'the world remembers history'; in the MVP it is an
    in-memory list (+ JSON snapshot) instead of a Postgres outbox table.
    """

    actor_id: str
    actor_name: str
    kind: str                          # ResultKind value, or "scene"/"system"/"narration"
    summary: str
    narration: str = ""
    scope: str = "local"               # personal | local | global (kept for design parity)
    data: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Event":
        return cls(**d)
