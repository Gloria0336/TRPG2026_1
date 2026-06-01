"""Two pre-made level-3 PCs — a martial and a caster — so combat and spells both get
exercised and newbies skip character creation entirely (onboarding goal)."""
from __future__ import annotations

from ..engine.types import (
    Ability,
    Action,
    ActionCost,
    ActionType,
    Character,
    Damage,
)


def _bram() -> Character:
    """Bram Ironwood — Human Fighter 3. Sturdy front-liner, simple to play."""
    return Character(
        id="pc_bram",
        name="Bram Ironwood",
        is_pc=True,
        level=3,
        abilities={"STR": 16, "DEX": 12, "CON": 16, "INT": 10, "WIS": 12, "CHA": 10},
        max_hp=28,
        hp=28,
        ac=18,  # chain mail + shield
        speed=30,
        skill_prof={"athletics": "prof", "perception": "prof", "intimidation": "prof"},
        save_prof=["STR", "CON"],
        portrait="⚔️",
        blurb="A steady sword-and-board fighter. When in doubt, hit it.",
        actions=[
            Action(
                name="Longsword",
                type=ActionType.MELEE_ATTACK,
                to_hit=5,  # STR +3, prof +2
                damage=[Damage(1, 8, 3, "slashing")],
                range_ft=5,
                description="A reliable melee swing.",
            ),
            Action(
                name="Heavy Crossbow",
                type=ActionType.RANGED_ATTACK,
                to_hit=3,  # DEX +1, prof +2
                damage=[Damage(1, 10, 1, "piercing")],
                range_ft=100,
                description="A ranged shot for distant foes.",
            ),
            Action(
                name="Second Wind",
                type=ActionType.HEAL,
                cost=ActionCost.BONUS,
                heal=Damage(1, 10, 3, "healing"),  # 1d10 + fighter level
                uses=1,
                range_ft=0,
                description="Catch a second breath, recovering some HP (once per fight).",
            ),
        ],
    )


def _lyra() -> Character:
    """Lyra Dawnbringer — Half-Elf Cleric 3 (Life). Healer + radiant damage + face."""
    return Character(
        id="pc_lyra",
        name="Lyra Dawnbringer",
        is_pc=True,
        level=3,
        abilities={"STR": 12, "DEX": 10, "CON": 14, "INT": 10, "WIS": 16, "CHA": 12},
        max_hp=24,
        hp=24,
        ac=16,  # scale mail + shield
        speed=30,
        skill_prof={"medicine": "prof", "insight": "prof", "persuasion": "prof", "religion": "prof"},
        save_prof=["WIS", "CHA"],
        portrait="✨",
        blurb="A warm-hearted cleric who keeps the party standing and smites the wicked.",
        actions=[
            Action(
                name="Mace",
                type=ActionType.MELEE_ATTACK,
                to_hit=3,  # STR +1, prof +2
                damage=[Damage(1, 6, 1, "bludgeoning")],
                range_ft=5,
                description="A simple bludgeon for when foes close in.",
            ),
            Action(
                name="Sacred Flame",
                type=ActionType.SAVE_SPELL,
                save_ability=Ability.DEX,
                save_dc=13,  # 8 + prof 2 + WIS 3
                save_effect="none",
                damage=[Damage(1, 8, 0, "radiant")],
                range_ft=60,
                description="Radiant fire descends; target makes a DEX save or takes 1d8 (at-will cantrip).",
            ),
            Action(
                name="Guiding Bolt",
                type=ActionType.RANGED_ATTACK,
                to_hit=5,  # spell attack: prof 2 + WIS 3
                damage=[Damage(4, 6, 0, "radiant")],
                uses=3,
                range_ft=120,
                description="A lance of light; spell attack for 4d6 radiant (limited casts).",
            ),
            Action(
                name="Cure Wounds",
                type=ActionType.HEAL,
                heal=Damage(1, 8, 3, "healing"),  # 1d8 + WIS
                uses=4,
                range_ft=5,
                description="Touch an ally to restore 1d8+3 HP (limited casts).",
            ),
            Action(
                name="Healing Word",
                type=ActionType.HEAL,
                cost=ActionCost.BONUS,
                heal=Damage(1, 4, 3, "healing"),  # 1d4 + WIS
                uses=2,
                range_ft=60,
                description="A shouted word of mending at range as a bonus action (limited casts).",
            ),
        ],
    )


def premade_pcs() -> list[Character]:
    """Fresh copies of the two pre-made PCs (call once per session)."""
    return [_bram(), _lyra()]
