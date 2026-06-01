"""Monster templates for the one-shot."""
from __future__ import annotations

from ..engine.types import Action, ActionType, Character, Damage


def _goblin() -> Character:
    """Standard 5e goblin (CR 1/4)."""
    return Character(
        id="goblin",
        name="Goblin",
        is_pc=False,
        level=1,
        abilities={"STR": 8, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 8},
        max_hp=7,
        hp=7,
        ac=15,
        speed=30,
        portrait="匕",
        blurb="矮小兇狠的掠奪者，擅長用卑鄙手段取勝。",
        actions=[
            Action("Scimitar", ActionType.MELEE_ATTACK, to_hit=4, damage=[Damage(1, 6, 2, "slashing")], range_ft=5),
            Action("Shortbow", ActionType.RANGED_ATTACK, to_hit=4, damage=[Damage(1, 6, 2, "piercing")], range_ft=80),
        ],
    )


def _goblin_boss() -> Character:
    """Goblin Boss (CR 1)."""
    return Character(
        id="goblin_boss",
        name="Grix the Goblin Boss",
        is_pc=False,
        level=2,
        abilities={"STR": 10, "DEX": 14, "CON": 10, "INT": 10, "WIS": 8, "CHA": 10},
        max_hp=21,
        hp=21,
        ac=17,
        speed=30,
        portrait="首",
        blurb="滿臉傷疤、詭計多端的首領，驅使手下向前送死。",
        actions=[
            Action("Scimitar Flurry", ActionType.MELEE_ATTACK, to_hit=4, damage=[Damage(2, 6, 2, "slashing")], range_ft=5),
            Action("Javelin", ActionType.RANGED_ATTACK, to_hit=2, damage=[Damage(1, 6, 0, "piercing")], range_ft=30),
        ],
    )


_TEMPLATES = {
    "goblin": _goblin,
    "goblin_boss": _goblin_boss,
}


def spawn(key: str, index: int = 1) -> Character:
    """Create a uniquely-id'd monster from a template, numbered when several appear."""
    template = _TEMPLATES[key]()
    if key == "goblin_boss":
        template.id = key
        return template
    template.id = f"{key}_{index}"
    template.name = f"{template.name} {index}"
    return template
