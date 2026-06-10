"""Validated schema for the intent-parser's JSON output.

The model's reply is parsed into this pydantic model; anything malformed is rejected
and retried/fallback-ed. The parser proposes a difficulty `band` (action-method
difficulty) plus a clamped `env_modifier` (scene/target difficulty); the engine composes
them via rules_5e.dc_from_band, so even stray numbers can't set an arbitrary DC.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..content import quest_taxonomy
from ..db.store import DISPOSITIONS, ENTITY_KINDS, ENTITY_STATUSES, ITEM_CATEGORIES
from ..engine import conditions as cond
from ..engine import rules_5e

# Difficulty bands the parser may pick for the action's *method* (design §4.3 — four-tier,
# normal = standard DC 10).
DIFFICULTY_BANDS = ["easy", "normal", "hard", "extreme"]

# PF2e skills the parser may pick as an `approach`.
ALLOWED_SKILLS = [
    "acrobatics", "arcana", "athletics", "crafting", "deception", "diplomacy",
    "intimidation", "medicine", "nature", "occultism", "perception", "performance",
    "religion", "society", "stealth", "survival", "thievery",
]


class DCAssessment(BaseModel):
    """Auditable DC breakdown: base (action-method band) + env_modifier (scene/target).

    `final_dc = max(MIN_DC, base_dc + env_modifier)` — computed by rules_5e.dc_from_band,
    intentionally NOT snapped to an anchor. Carried alongside the Intent so the engine
    and event log can record *why* a DC landed where it did.
    """

    base_dc: int
    env_modifier: int
    final_dc: int
    env_reason: str | None = None


class IntentParse(BaseModel):
    """Structured intent the parser must return (design §8.3)."""

    tier: Literal["A", "B", "C"]
    action: str | None = None
    target: str | None = None
    approach: str | None = None              # a PF2e skill name when known
    # The specific subject of the action — what is being asked about, examined, or
    # talked into. Optional; only set when the player named one. Lets the narrator
    # render "asks about her underwear color" instead of just "asks a question".
    topic: str | None = None
    is_attack: bool = False                  # True if this is an attempt to attack/fight
    # Tier-A only: does this action need a d20 check, or is it a trivial/uncontested
    # beat that simply happens? The model proposes; the engine's gate (resolution.
    # requires_check) can override false → true so player narration can never talk its
    # way past a contested/risky check (design §8.3 anti-talk protection).
    needs_check: bool = True
    candidates: list[str] = Field(default_factory=list)   # legacy/off-spec hints; not buttons
    question: str | None = None              # tier C: one clarifying question
    options: list[str] = Field(default_factory=list)      # tier C: option labels
    # DC = base (action-method difficulty band) + env_modifier (scene/target difficulty).
    # `difficulty_band` is null for on-table actions (scene fixed DC) or when no roll is
    # needed; the engine then uses its default. Tools/allies/resources are NEVER folded in
    # here — those are applied to the player's roll by the engine (§4.9).
    difficulty_band: Literal[
        "easy", "normal", "hard", "extreme"
    ] | None = None
    env_modifier: int = 0                    # scene/target offset, clamped to ±ENV_MODIFIER_CAP
    env_reason: str | None = None            # short audit note for the env_modifier
    # True when the message relies on a false premise — gear the actor does not carry, or a
    # fact not established in the scene (e.g. "I detonate the C4 I hid earlier"). The bot
    # then gives an in-world redirect instead of an options menu that would legitimise it.
    implausible: bool = False

    # The model often returns `null` for unused list fields (tier A has no candidates,
    # tier A/B has no options). Coerce nulls to empty lists so validation doesn't fail.
    @field_validator("candidates", "options", mode="before")
    @classmethod
    def _none_to_empty(cls, value: object) -> object:
        return [] if value is None else value

    @field_validator("env_modifier", mode="before")
    @classmethod
    def _env_modifier_default(cls, value: object) -> int:
        # A creative model may omit it or send null; treat as no offset. Hard clamping
        # to ±cap happens in dc_from_band so the stored value is always in range.
        if value is None:
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def dc_assessment(self) -> "DCAssessment | None":
        """Compose the auditable DC breakdown, or None when no band was proposed
        (on-table / no-roll actions fall back to the engine's default)."""
        if self.difficulty_band is None:
            return None
        final, base, env = rules_5e.dc_from_band(self.difficulty_band, self.env_modifier)
        return DCAssessment(
            base_dc=base, env_modifier=env, final_dc=final, env_reason=self.env_reason
        )


# ───────────────────────── Entity-state extraction ─────────────────────────
class EntityStateDelta(BaseModel):
    """One narrative-state change the extractor pulls from a narration (design §6.0
    state markers). It NEVER touches numbers/HP/success — only who is where and how.
    Invalid enum values are coerced to None so a creative model can't widen the vocab.
    """

    entity_ref: str | None = None        # name/alias of an existing entity in the scene
    status: str | None = None            # one of ENTITY_STATUSES
    disposition: str | None = None       # one of DISPOSITIONS
    location_id: str | None = None
    note: str | None = None              # short fact to append to the entity's notes
    # A PROMISE / standing fact / attitude shift this entity just made toward the party
    # that must persist regardless of the event window — e.g. "答應帶路去地窖",
    # "從此視玩家為盟友", "供出了走私船的名字". Stored on the entity's flags and
    # re-injected every turn (like agenda), so the NPC never contradicts it later.
    commitment: str | None = None
    # Mechanical conditions the narration newly describes (e.g. the figure "was
    # tied up" → add_conditions=["restrained"]; "woke up" → remove_conditions=
    # ["unconscious"]). Unknown ids are filtered server-side; the model can't
    # invent new mechanics this way.
    add_conditions: list[str] = Field(default_factory=list)
    remove_conditions: list[str] = Field(default_factory=list)
    # When the narration introduces a brand-new entity, the model may register it:
    register_kind: str | None = None     # one of ENTITY_KINDS → triggers registration
    register_name: str | None = None
    aliases: list[str] = Field(default_factory=list)

    @field_validator("status")
    @classmethod
    def _valid_status(cls, v: str | None) -> str | None:
        return v if v in ENTITY_STATUSES else None

    @field_validator("disposition")
    @classmethod
    def _valid_disposition(cls, v: str | None) -> str | None:
        return v if v in DISPOSITIONS else None

    @field_validator("register_kind")
    @classmethod
    def _valid_kind(cls, v: str | None) -> str | None:
        return v if v in ENTITY_KINDS else None

    @field_validator("aliases", "add_conditions", "remove_conditions", mode="before")
    @classmethod
    def _none_to_empty(cls, value: object) -> object:
        return [] if value is None else value

    @field_validator("add_conditions", "remove_conditions")
    @classmethod
    def _drop_unknown_conditions(cls, value: list[str]) -> list[str]:
        # Filter out anything the catalog doesn't know; a creative model can't
        # widen the mechanical vocabulary by inventing flag names.
        return [c for c in value if isinstance(c, str) and cond.known(c)]

    def is_noop(self) -> bool:
        """True when the delta carries nothing actionable."""
        if self.register_kind and (self.register_name or self.entity_ref):
            return False
        if not self.entity_ref:
            return True
        return not any((
            self.status, self.disposition, self.location_id, self.note,
            self.commitment, self.add_conditions, self.remove_conditions,
        ))


class ItemGrant(BaseModel):
    """One acquired item extracted from narration."""

    item_name: str
    recipient_ref: str | None = None
    quantity: int = 1
    category: str | None = None
    source_ref: str | None = None

    @field_validator("quantity", mode="before")
    @classmethod
    def _positive_quantity(cls, value: object) -> int:
        try:
            qty = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 1
        return max(1, qty)

    @field_validator("category")
    @classmethod
    def _valid_category(cls, value: str | None) -> str | None:
        return value if value in ITEM_CATEGORIES else None

    def is_noop(self) -> bool:
        return not bool((self.item_name or "").strip())


class EntityExtraction(BaseModel):
    """The extractor's full reply: a list of state deltas (may be empty), plus an
    optional lasting change to the PLACE itself (not tied to any one entity)."""

    deltas: list[EntityStateDelta] = Field(default_factory=list)
    item_grants: list[ItemGrant] = Field(default_factory=list)
    # A persistent environmental change to the current location — e.g. "絆線已被拆除",
    # "地上的水囊潑灑了一地". Folded into the location's state and shown on revisits.
    location_note: str | None = None

    @field_validator("deltas", "item_grants", mode="before")
    @classmethod
    def _none_to_empty(cls, value: object) -> object:
        return [] if value is None else value

    def actionable(self) -> list[EntityStateDelta]:
        return [d for d in self.deltas if not d.is_noop()]

    def acquired_items(self) -> list[ItemGrant]:
        return [g for g in self.item_grants if not g.is_noop()]


class QuestSeed(BaseModel):
    """Small GM-authored quest seed for the background quest agent."""

    seed_id: str | None = None
    giver: str = ""
    title_hint: str = ""
    premise: str = ""
    objective_hint: str = ""
    reward_hint: str = ""
    acceptance_mode: Literal["direct_accept", "requires_check"] = "direct_accept"
    required_check: str | None = None
    known_constraints: str = ""
    named_entities: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    stakes: str = ""
    tags: dict[str, str] = Field(default_factory=dict)
    dedupe_key: str = ""

    @field_validator("named_entities", "locations", mode="before")
    @classmethod
    def _seed_none_to_empty(cls, value: object) -> object:
        return [] if value is None else value

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_seed_tags(cls, value: object) -> dict[str, str]:
        return quest_taxonomy.normalize_tags(value if isinstance(value, dict) else {})

    def should_create(self) -> bool:
        return bool((self.giver or "").strip() and (self.premise or self.objective_hint or self.title_hint))


class NarrationQuestEnvelope(BaseModel):
    """GM narration payload: prose plus an optional quest seed."""

    prose: str = ""
    quest_offer: QuestSeed | None = None


class QuestDetails(BaseModel):
    """Executable quest card produced by the quest agent."""

    title: str = ""
    giver: str = ""
    objective: str = ""
    known_info: list[str] = Field(default_factory=list)
    details: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    success_conditions: list[str] = Field(default_factory=list)
    failure_risks: list[str] = Field(default_factory=list)
    reward: str = ""
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator(
        "known_info", "details", "next_steps", "success_conditions", "failure_risks",
        mode="before",
    )
    @classmethod
    def _details_none_to_empty(cls, value: object) -> object:
        return [] if value is None else value

    @field_validator("tags", mode="before")
    @classmethod
    def _normalize_detail_tags(cls, value: object) -> dict[str, str]:
        return quest_taxonomy.normalize_tags(value if isinstance(value, dict) else {})


class LocationCard(BaseModel):
    """Stable generated anchors for an entity-backed location."""

    canonical_name: str = ""
    aliases: list[str] = Field(default_factory=list)
    base_summary: str = ""
    sensory_anchors: list[str] = Field(default_factory=list)
    visual_landmarks: list[str] = Field(default_factory=list)
    interactive_features: list[str] = Field(default_factory=list)
    discoverables: list[str] = Field(default_factory=list)
    hazards: list[str] = Field(default_factory=list)
    soft_hooks: list[str] = Field(default_factory=list)
    exits_hint: list[str] = Field(default_factory=list)
    mood: str = ""
    terrain_modifier: float = 1.0

    @field_validator(
        "aliases",
        "sensory_anchors",
        "visual_landmarks",
        "interactive_features",
        "discoverables",
        "hazards",
        "soft_hooks",
        "exits_hint",
        mode="before",
    )
    @classmethod
    def _location_lists_none_to_empty(cls, value: object) -> object:
        return [] if value is None else value

    @field_validator("terrain_modifier", mode="before")
    @classmethod
    def _terrain_modifier_bounds(cls, value: object) -> float:
        try:
            parsed = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            parsed = 1.0
        return max(0.3, min(2.0, parsed))


_KNOWN_CONDITION_IDS = sorted(cond.CATALOG.keys())


EXTRACT_JSON_SHAPE = (
    '{\n'
    '  "deltas": [\n'
    '    {\n'
    '      "entity_ref": existing entity name/alias this change is about | null,\n'
    f'      "status": one of {list(ENTITY_STATUSES)} | null,\n'
    f'      "disposition": one of {list(DISPOSITIONS)} | null,\n'
    '      "note": one short factual clause to remember | null,\n'
    '      "commitment": a promise/standing-fact/attitude-shift this NPC just made toward the party that must persist (e.g. "答應帶路去地窖") | null,\n'
    f'      "add_conditions": subset of {_KNOWN_CONDITION_IDS} | []  // attach mechanical flags the narration newly implies,\n'
    '      "remove_conditions": [condition ids the narration shows are gone] | [],\n'
    f'      "register_kind": one of {list(ENTITY_KINDS)} | null  // only for a NEW entity,\n'
    '      "register_name": name of the new entity | null,\n'
    '      "aliases": [other names for the new entity]\n'
    '    }\n'
    '  ],\n'
    '  "item_grants": [\n'
    '    {\n'
    '      "item_name": item that was actually acquired/transferred/rewarded,\n'
    '      "recipient_ref": actor name/alias receiving it, or null for the acting PC,\n'
    '      "quantity": positive integer, default 1,\n'
    f'      "category": one of {list(ITEM_CATEGORIES)} | null,\n'
    '      "source_ref": scene entity/source that plausibly provided it | null\n'
    '    }\n'
    '  ],\n'
    '  "location_note": one lasting change to the PLACE itself, not tied to a person/object (e.g. "絆線已被拆除") | null\n'
    '}'
)


# JSON schema string embedded in the prompt to steer the model.
INTENT_JSON_SHAPE = (
    '{\n'
    '  "tier": "A" | "C",\n'
    '  "action": short verb like "persuade" | "search" | "attack" | null,\n'
    '  "target": who/what is targeted, or null,\n'
    '  "approach": one PF2e skill from the allowed list, or null,\n'
    '  "topic": the specific subject of the action when the player named one (e.g. "內褲顏色", "商隊去向", "他的名字"), or null,\n'
    '  "is_attack": true if the player is trying to attack/fight, else false,\n'
    '  "needs_check": false ONLY for a trivial, uncontested, no-risk action; else true,\n'
    '  "candidates": [],                             // retired; always return []\n'
    '  "question": one short clarifying question | null  // tier C only,\n'
    '  "options": [2-4 option labels]                 // tier C only,\n'
    '  "difficulty_band": "easy|normal|hard|extreme" | null,  // how hard the player\'s chosen METHOD is (normal=標準 DC10); null for on-table/no-roll\n'
    '  "env_modifier": integer in -4..+4,             // scene/target difficulty: favourable→negative, hostile→positive\n'
    '  "env_reason": short reason for env_modifier | null,\n'
    '  "implausible": true if the message relies on gear the actor lacks or a fact not in the scene, else false\n'
    '}'
)


QUEST_SEED_JSON_SHAPE = (
    '{\n'
    '  "prose": "Traditional Chinese GM narration",\n'
    '  "quest_offer": {\n'
    '    "seed_id": short stable id or null,\n'
    '    "giver": NPC name issuing the task,\n'
    '    "title_hint": short quest title hint,\n'
    '    "premise": one-sentence situation,\n'
    '    "objective_hint": what the party is being asked to do,\n'
    '    "reward_hint": promised reward or empty string,\n'
    '    "acceptance_mode": "direct_accept" | "requires_check",\n'
    '    "required_check": skill/check needed before acceptance or null,\n'
    '    "known_constraints": limits, deadline, taboo, route, or empty string,\n'
    '    "named_entities": [important people/objects],\n'
    '    "locations": [important places],\n'
    '    "stakes": what worsens if ignored,\n'
    '    "tags": eight-axis fixed taxonomy object,\n'
    '    "dedupe_key": stable lowercase key for this quest\n'
    '  } | null\n'
    '}'
)


QUEST_DETAILS_JSON_SHAPE = (
    '{\n'
    '  "title": quest title,\n'
    '  "giver": quest giver,\n'
    '  "objective": concrete objective,\n'
    '  "known_info": [facts already revealed],\n'
    '  "details": [stable details NPCs must not contradict],\n'
    '  "next_steps": [immediate player-facing leads],\n'
    '  "success_conditions": [what counts as completion],\n'
    '  "failure_risks": [what happens if ignored/fails],\n'
    '  "reward": reward text,\n'
    '  "tags": eight-axis fixed taxonomy object\n'
    '}'
)


LOCATION_CARD_JSON_SHAPE = (
    '{\n'
    '  "canonical_name": location official name,\n'
    '  "aliases": [usable alternate names],\n'
    '  "base_summary": "2-4 stable Traditional Chinese scene-description sentences",\n'
    '  "sensory_anchors": [smell, sound, temperature, light, texture anchors],\n'
    '  "visual_landmarks": [repeatable visual landmarks],\n'
    '  "interactive_features": [environmental things players can naturally interact with],\n'
    '  "discoverables": [non-numeric clues that exploration might reveal],\n'
    '  "hazards": [non-numeric danger hints, no DCs or damage],\n'
    '  "soft_hooks": [scene hooks that can guide the next action],\n'
    '  "exits_hint": [narrative connections to known paths or directions],\n'
    '  "mood": overall atmosphere,\n'
    '  "terrain_modifier": 1.0  // travel speed multiplier: 1 normal, <1 rough, >1 easy\n'
    '}'
)
