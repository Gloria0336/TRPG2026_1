"""Validated schema for the intent-parser's JSON output.

The model's reply is parsed into this pydantic model; anything malformed is rejected
and retried/fallback-ed. `suggested_dc` is further snapped to a 5e anchor by the engine,
so even a stray number cannot set an arbitrary DC.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ..db.store import DISPOSITIONS, ENTITY_KINDS, ENTITY_STATUSES
from ..engine.rules_5e import DC_ANCHORS

# 5e skills the parser may pick as an `approach`.
ALLOWED_SKILLS = [
    "acrobatics", "animal_handling", "arcana", "athletics", "deception", "history",
    "insight", "intimidation", "investigation", "medicine", "nature", "perception",
    "performance", "persuasion", "religion", "sleight_of_hand", "stealth", "survival",
]


class IntentParse(BaseModel):
    """Structured intent the parser must return (design §8.3)."""

    tier: Literal["A", "B", "C"]
    action: str | None = None
    target: str | None = None
    approach: str | None = None              # a 5e skill name when known
    is_attack: bool = False                  # True if this is an attempt to attack/fight
    # Tier-A only: does this action need a d20 check, or is it a trivial/uncontested
    # beat that simply happens? The model proposes; the engine's gate (resolution.
    # requires_check) can override false → true so player narration can never talk its
    # way past a contested/risky check (design §8.3 anti-talk protection).
    needs_check: bool = True
    candidates: list[str] = Field(default_factory=list)   # tier B: 2-4 candidate methods
    question: str | None = None              # tier C: one clarifying question
    options: list[str] = Field(default_factory=list)      # tier C: option labels
    suggested_dc: int | None = None          # only used for off-table actions
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

    def snapped_dc(self) -> int | None:
        if self.suggested_dc is None:
            return None
        return min(DC_ANCHORS, key=lambda a: abs(a - self.suggested_dc))


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

    @field_validator("aliases", mode="before")
    @classmethod
    def _none_to_empty(cls, value: object) -> object:
        return [] if value is None else value

    def is_noop(self) -> bool:
        """True when the delta carries nothing actionable."""
        if self.register_kind and (self.register_name or self.entity_ref):
            return False
        if not self.entity_ref:
            return True
        return not any((self.status, self.disposition, self.location_id, self.note))


class EntityExtraction(BaseModel):
    """The extractor's full reply: a list of state deltas (may be empty)."""

    deltas: list[EntityStateDelta] = Field(default_factory=list)

    @field_validator("deltas", mode="before")
    @classmethod
    def _none_to_empty(cls, value: object) -> object:
        return [] if value is None else value

    def actionable(self) -> list[EntityStateDelta]:
        return [d for d in self.deltas if not d.is_noop()]


EXTRACT_JSON_SHAPE = (
    '{\n'
    '  "deltas": [\n'
    '    {\n'
    '      "entity_ref": existing entity name/alias this change is about | null,\n'
    f'      "status": one of {list(ENTITY_STATUSES)} | null,\n'
    f'      "disposition": one of {list(DISPOSITIONS)} | null,\n'
    '      "note": one short factual clause to remember | null,\n'
    f'      "register_kind": one of {list(ENTITY_KINDS)} | null  // only for a NEW entity,\n'
    '      "register_name": name of the new entity | null,\n'
    '      "aliases": [other names for the new entity]\n'
    '    }\n'
    '  ]\n'
    '}'
)


# JSON schema string embedded in the prompt to steer the model.
INTENT_JSON_SHAPE = (
    '{\n'
    '  "tier": "A" | "B" | "C",\n'
    '  "action": short verb like "persuade" | "search" | "attack" | null,\n'
    '  "target": who/what is targeted, or null,\n'
    '  "approach": one 5e skill from the allowed list, or null,\n'
    '  "is_attack": true if the player is trying to attack/fight, else false,\n'
    '  "needs_check": false ONLY for a trivial, uncontested, no-risk action; else true,\n'
    '  "candidates": [2-4 concrete method options]   // tier B only,\n'
    '  "question": one short clarifying question | null  // tier C only,\n'
    '  "options": [2-4 option labels]                 // tier C only,\n'
    '  "suggested_dc": 5|10|15|20|25|30|35 | null,    // only for unusual off-table actions\n'
    '  "implausible": true if the message relies on gear the actor lacks or a fact not in the scene, else false\n'
    '}'
)
