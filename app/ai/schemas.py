"""Validated schema for the intent-parser's JSON output.

The model's reply is parsed into this pydantic model; anything malformed is rejected
and retried/fallback-ed. `suggested_dc` is further snapped to a 5e anchor by the engine,
so even a stray number cannot set an arbitrary DC.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

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
    candidates: list[str] = Field(default_factory=list)   # tier B: 2-4 candidate methods
    question: str | None = None              # tier C: one clarifying question
    options: list[str] = Field(default_factory=list)      # tier C: option labels
    suggested_dc: int | None = None          # only used for off-table actions

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


# JSON schema string embedded in the prompt to steer the model.
INTENT_JSON_SHAPE = (
    '{\n'
    '  "tier": "A" | "B" | "C",\n'
    '  "action": short verb like "persuade" | "search" | "attack" | null,\n'
    '  "target": who/what is targeted, or null,\n'
    '  "approach": one 5e skill from the allowed list, or null,\n'
    '  "is_attack": true if the player is trying to attack/fight, else false,\n'
    '  "candidates": [2-4 concrete method options]   // tier B only,\n'
    '  "question": one short clarifying question | null  // tier C only,\n'
    '  "options": [2-4 option labels]                 // tier C only,\n'
    '  "suggested_dc": 5|10|15|20|25|30|35 | null     // only for unusual off-table actions\n'
    '}'
)
