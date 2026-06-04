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


# PF2e skill → governing (key) ability (design §4.2). Perception is kept here too so the
# engine can compute its bonus uniformly, though in PF2e it is a separate proficiency.
# Note vs 5e: Nature/Religion are WIS-keyed; Diplomacy replaces Persuasion, Thievery
# replaces Sleight of Hand, Society absorbs History/Recall Knowledge; Investigation and
# Insight fold into Perception (Seek / Sense Motive), Animal Handling into Nature.
SKILLS: dict[str, Ability] = {
    "acrobatics": Ability.DEX,
    "arcana": Ability.INT,
    "athletics": Ability.STR,
    "crafting": Ability.INT,
    "deception": Ability.CHA,
    "diplomacy": Ability.CHA,
    "intimidation": Ability.CHA,
    "medicine": Ability.WIS,
    "nature": Ability.WIS,
    "occultism": Ability.INT,
    "perception": Ability.WIS,
    "performance": Ability.CHA,
    "religion": Ability.WIS,
    "society": Ability.INT,
    "stealth": Ability.DEX,
    "survival": Ability.WIS,
    "thievery": Ability.DEX,
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
    movement_base: float = 4.0
    is_vehicle: bool = False
    vehicle_type: str | None = None
    # skill name -> "prof" | "expertise"  (absent = not proficient)
    skill_prof: dict[str, str] = field(default_factory=dict)
    save_prof: list[str] = field(default_factory=list)  # proficient saving-throw abilities
    actions: list[Action] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    # Per-condition data (level / source / duration). Parallel to `conditions`;
    # key is the condition id (incl. parametric "loyal_to:X"). See app.engine.conditions.
    condition_meta: dict[str, dict] = field(default_factory=dict)
    # 5e death saving throws (PCs only): successes/failures, and a "stable"/"dead" marker
    death_successes: int = 0
    death_failures: int = 0
    portrait: str = "🧝"                        # emoji shown in embeds/dashboard
    blurb: str = ""                             # one-line flavour for onboarding
    # Carried items (free-text). Read by the intent parser so a player can't conjure gear
    # they don't have (design: AI never invents facts). Not a full PF2e inventory yet.
    inventory: list[str] = field(default_factory=list)

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
    # The specific subject of the action when the player named one — e.g. for
    # "詢問兜帽客內褲顏色" the topic is "內褲顏色". Surfaced to the narrator so
    # prose stays literal instead of collapsing to a generic "they ask a question".
    topic: str | None = None
    is_attack: bool = False            # True if the player is attempting to attack/fight
    # Tier-A only: True (default) → roll a d20 check; False → trivial/uncontested beat
    # resolved without a roll. The engine gate (resolution.requires_check) is the final
    # arbiter and may force this back to True (design §8.3).
    needs_check: bool = True
    candidates: list[str] = field(default_factory=list)  # tier B: candidate approaches
    question: str | None = None        # tier C: clarifying question
    options: list[str] = field(default_factory=list)     # tier C: option labels
    # True when the message rests on a false premise (gear the actor lacks / a fact not in
    # the scene). The bot redirects in-world instead of resolving or offering a menu.
    implausible: bool = False

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


class ResultBand(str, Enum):
    """Four-degree outcome for ability checks (design §4.4 — PF2e symmetric ±10).

    Margin vs DC slices the outcome into four degrees of success:
    - CRIT_SUCCESS: total ≥ DC + 10 — a decisive success; a structured boon attaches
    - SUCCESS:      total ≥ DC
    - FAILURE:      total < DC — the attempt fails; a structured cost attaches
    - CRIT_FAILURE: total ≤ DC - 10 — a disastrous failure; a heavier cost attaches
    Nat 20 shifts the degree up one step, nat 1 shifts it down (capped at the ends).
    """

    CRIT_SUCCESS = "crit_success"
    SUCCESS = "success"
    FAILURE = "failure"
    CRIT_FAILURE = "crit_failure"

    @classmethod
    def _missing_(cls, value: object) -> "ResultBand | None":
        # Back-compat: legacy snapshots/event_logs stored the old three-band model.
        # "partial" counted as success-with-cost, so fold it onto SUCCESS on load.
        if value == "partial":
            return cls.SUCCESS
        return None


class CostType(str, Enum):
    """Structured cost categories (design §4.7). The engine picks the type from the
    scene's cost pool or a default skill-based fallback; the narrator turns it into
    fiction but never invents which type applied."""

    TIME = "time"             # 時間流逝
    EXPOSURE = "exposure"     # 行蹤暴露
    RESOURCE = "resource"     # 資源損耗
    TRACE = "trace"           # 留下痕跡
    ATTENTION = "attention"   # 引來注意
    RELATION = "relation"     # 關係惡化
    DEBT = "debt"             # 延遲後果（債務式）


class CostSeverity(str, Enum):
    LIGHT = "light"
    MODERATE = "moderate"
    HEAVY = "heavy"


@dataclass
class Cost:
    """A single structured cost attached to a FAILURE or CRIT_FAILURE result (design §4.7).

    `persistent` marks costs that should flow into durable state (flags, hp, resources)
    rather than living only in the narration. Out-of-combat MVP scope keeps most costs
    non-persistent until we wire scene-state effects in a later pass.
    """

    type: CostType
    severity: CostSeverity
    persistent: bool = False
    note: str = ""   # short program-generated tag; narrator elaborates, never overrides

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "severity": self.severity.value,
            "persistent": self.persistent,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Cost":
        return cls(
            type=CostType(d["type"]),
            severity=CostSeverity(d["severity"]),
            persistent=bool(d.get("persistent", False)),
            note=str(d.get("note", "")),
        )


class BoonType(str, Enum):
    """Structured benefit categories (design §4.4 大成功額外效果). The symmetric inverse
    of CostType: a CRIT_SUCCESS attaches one of these, picked by the engine from the
    scene's boon pool or a skill-based fallback. The narrator dramatizes it but never
    invents which type applied."""

    TIME_SAVED = "time_saved"        # 省時
    EXTRA_INFO = "extra_info"        # 額外情報
    PROGRESS = "progress"            # 進度加成
    RESOURCE_GAIN = "resource_gain"  # 資源回收
    GOODWILL = "goodwill"            # 關係改善
    OPENING = "opening"              # 創造良機


class BoonMagnitude(str, Enum):
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"


@dataclass
class Boon:
    """A single structured benefit attached to a CRIT_SUCCESS result (design §4.4).

    Mirrors `Cost`: `persistent` marks boons that should flow into durable state
    (flags, resources, progress) rather than living only in the narration.
    """

    type: BoonType
    magnitude: BoonMagnitude
    persistent: bool = False
    note: str = ""   # short program-generated tag; narrator elaborates, never overrides

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "magnitude": self.magnitude.value,
            "persistent": self.persistent,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Boon":
        return cls(
            type=BoonType(d["type"]),
            magnitude=BoonMagnitude(d["magnitude"]),
            persistent=bool(d.get("persistent", False)),
            note=str(d.get("note", "")),
        )


@dataclass
class ResolutionResult:
    """Structured outcome computed entirely by the engine. The AI turns this into
    prose but must not change any number here (design §4.0 / §4.6)."""

    kind: ResultKind
    actor_id: str
    actor_name: str
    summary: str                       # terse mechanical summary, e.g. "Athletics check vs DC 15: SUCCESS"
    # `success` is the boolean back-compat view: a success degree (CRIT_SUCCESS/SUCCESS)
    # → True. FAILURE / CRIT_FAILURE → False, NARRATIVE → None.
    success: bool | None = None
    band: ResultBand | None = None    # four-degree outcome for CHECK results (§4.4)
    cost: Cost | None = None          # attached when band is FAILURE or CRIT_FAILURE (§4.7)
    boon: Boon | None = None          # attached when band is CRIT_SUCCESS (§4.4 大成功額外效果)
    target_id: str | None = None
    target_name: str | None = None
    # The specific subject of the action — copied through from Intent.topic so
    # the narrator can stay literal ("asks about her underwear color") rather
    # than collapsing to a generic verb.
    topic: str | None = None
    dc: int | None = None
    # DC breakdown for auditing (design §4.3 — final DC = base band + env modifier).
    # `dc` is the final value; these record why it landed there. None for non-check or
    # scene-fixed-DC results.
    dc_base: int | None = None
    dc_env_modifier: int | None = None
    dc_env_reason: str | None = None
    # Deterministic disposition offset applied to social checks against a dispositioned
    # NPC (design: 態度數值化). None for non-social checks or targets without a disposition.
    dc_npc_modifier: int | None = None
    dc_npc_disposition: str | None = None
    roll_breakdown: str | None = None  # human-readable dice breakdown for the embed
    natural: int | None = None
    crit: bool = False
    fumble: bool = False
    damage: int | None = None
    healing: int | None = None
    deltas: list[str] = field(default_factory=list)   # plain-language state changes applied
    # Hint to the narrator (style/tone), NOT a number it may alter.
    narration_hint: str = ""
    # The player's original natural-language utterance, carried through so the narrator
    # knows who/what the actor was actually addressing (combat fills this from the
    # action choice; resolution.resolve fills it from intent.raw_text).
    raw_text: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["band"] = self.band.value if self.band else None
        d["cost"] = self.cost.to_dict() if self.cost else None
        d["boon"] = self.boon.to_dict() if self.boon else None
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
