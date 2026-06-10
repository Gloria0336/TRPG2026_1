"""Static object affordances for intent-context grounding.

The intent parser may use this data to understand what a scene object is made of,
what it can plausibly be used for, and which safe mechanical condition ids a listed
effect maps to. It never decides success, DCs, damage, or quantities.
"""
from __future__ import annotations

import re
from copy import deepcopy

from . import items
from ..engine import conditions as cond


Affordance = dict[str, list[str]]

AFFORDANCES: dict[str, Affordance] = {
    "torch": {
        "material": ["wood", "oil-soaked cloth", "flame"],
        "can_be": ["light", "wave", "throw", "drop", "use as a signal"],
        "effects": ["illuminate", "ignite", "create smoke", "draw attention"],
        "tags": ["flammable", "portable", "light_source"],
    },
    "油燈": {
        "material": ["metal", "glass", "lamp oil", "flame"],
        "can_be": ["light", "extinguish", "tip over", "throw", "carry"],
        "effects": ["illuminate", "ignite", "create smoke", "make floor slick", "draw attention"],
        "tags": ["flammable", "fragile", "light_source"],
    },
    "酒杯": {
        "material": ["wood or clay cup", "alcohol"],
        "can_be": ["drink", "spill", "throw", "offer", "hide small items behind"],
        "effects": ["make floor slick", "distract npc", "draw attention"],
        "tags": ["portable", "liquid", "social"],
    },
    "吧台": {
        "material": ["heavy wood", "scratched counter"],
        "can_be": ["lean on", "hide behind", "place objects on", "start conversation at"],
        "effects": ["provide cover", "create social opening", "block movement"],
        "tags": ["fixture", "cover", "social"],
    },
    "桌子": {
        "material": ["wood", "loose crockery"],
        "can_be": ["hide under", "overturn", "climb onto", "search"],
        "effects": ["provide cover", "block movement", "draw attention"],
        "tags": ["fixture", "cover"],
    },
    "椅子": {
        "material": ["wood"],
        "can_be": ["sit on", "move", "throw", "wedge under a handle"],
        "effects": ["block movement", "trip someone", "draw attention"],
        "tags": ["portable", "improvised_tool"],
    },
    "窗簾": {
        "material": ["cloth", "dust"],
        "can_be": ["pull down", "wrap around", "hide behind", "ignite"],
        "effects": ["restrain", "blind", "ignite", "create smoke"],
        "tags": ["flammable", "flexible", "cover"],
    },
    "繩子": {
        "material": ["hemp fiber"],
        "can_be": ["tie", "climb", "pull", "trip", "anchor"],
        "effects": ["restrain", "grapple", "trip someone", "secure object"],
        "tags": ["flexible", "portable", "tool"],
    },
    "貨車": {
        "material": ["wood", "iron fittings", "canvas"],
        "can_be": ["search", "climb onto", "hide behind", "push", "block a path"],
        "effects": ["provide cover", "block movement", "make noise", "reveal supplies"],
        "tags": ["large", "cover", "container"],
    },
    "散箱": {
        "material": ["wood", "straw packing"],
        "can_be": ["search", "stack", "break", "hide behind", "move"],
        "effects": ["provide cover", "block movement", "make noise", "reveal supplies"],
        "tags": ["container", "fragile", "cover"],
    },
    "陷阱": {
        "material": ["wire", "spring", "hidden trigger"],
        "can_be": ["notice", "disable", "avoid", "trigger deliberately"],
        "effects": ["restrain", "prone", "draw attention"],
        "tags": ["hazard", "hidden", "mechanism"],
    },
    "陰影": {
        "material": ["darkness", "cover"],
        "can_be": ["hide in", "move through", "watch from"],
        "effects": ["conceal movement", "create opening"],
        "tags": ["cover", "stealth"],
    },
    "岩柱": {
        "material": ["stone", "rough surface"],
        "can_be": ["hide behind", "climb", "circle around", "brace against"],
        "effects": ["provide cover", "block movement", "create opening"],
        "tags": ["cover", "fixture", "stone"],
    },
}

ALIASES: dict[str, str] = {
    "torches": "torch",
    "火把": "torch",
    "一支火把": "torch",
    "油灯": "油燈",
    "oil lamp": "油燈",
    "lamp": "油燈",
    "酒": "酒杯",
    "酒杯": "酒杯",
    "wine": "酒杯",
    "cup": "酒杯",
    "bar": "吧台",
    "counter": "吧台",
    "可請人喝酒攀談的吧台": "吧台",
    "table": "桌子",
    "鄰桌": "桌子",
    "可旁聽的鄰桌": "桌子",
    "chair": "椅子",
    "curtain": "窗簾",
    "drape": "窗簾",
    "rope": "繩子",
    "蝜拍揣": "繩子",
    "wagon": "貨車",
    "cart": "貨車",
    "可翻找的貨車與散箱": "貨車",
    "crate": "散箱",
    "box": "散箱",
    "trap": "陷阱",
    "可拆解或迴避的陷阱": "陷阱",
    "shadow": "陰影",
    "可潛行繞行的陰影": "陰影",
    "pillar": "岩柱",
    "可掩護移動的岩柱": "岩柱",
}

EFFECT_CONDITIONS: dict[str, list[str]] = {
    "restrain": [cond.RESTRAINED],
    "grapple": [cond.GRAPPLED],
    "trip someone": [cond.PRONE],
    "prone": [cond.PRONE],
    "blind": [cond.BLINDED],
    "distract npc": [cond.NPC_DISTRACTED],
    "npc_distracted": [cond.NPC_DISTRACTED],
}

_FEATURE_PREFIXES = (
    "可",
    "能",
    "可以",
)


def normalize_name(name: str) -> str:
    """Canonicalize object/free-text feature names for affordance lookup."""
    text = items.normalize_name(name)
    text = re.sub(r"[，,;；、/]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for prefix in _FEATURE_PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text


def lookup(name: str) -> Affordance | None:
    """Return a copy of the best matching affordance card for a name or phrase."""
    norm = normalize_name(name)
    if not norm:
        return None
    direct = ALIASES.get(norm, norm)
    if direct in AFFORDANCES:
        return deepcopy(AFFORDANCES[direct])
    for alias, canonical in ALIASES.items():
        alias_norm = normalize_name(alias)
        if alias_norm and alias_norm in norm:
            return deepcopy(AFFORDANCES[canonical])
    for canonical, card in AFFORDANCES.items():
        canonical_norm = normalize_name(canonical)
        if canonical_norm and canonical_norm in norm:
            return deepcopy(card)
        for value in card.get("material", []) + card.get("tags", []):
            value_norm = normalize_name(value)
            if value_norm and value_norm == norm:
                return deepcopy(card)
    return None


def effects_to_conditions(effect: str) -> list[str]:
    """Map an affordance effect phrase to known condition ids only."""
    norm = normalize_name(effect)
    out: list[str] = []
    for key, condition_ids in EFFECT_CONDITIONS.items():
        key_norm = normalize_name(key)
        if key_norm and (norm == key_norm or key_norm in norm):
            out.extend(cid for cid in condition_ids if cond.known(cid))
    seen: list[str] = []
    for cid in out:
        if cid not in seen:
            seen.append(cid)
    return seen
