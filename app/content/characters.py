"""Two pre-made level-3 PCs: a martial character and a caster."""
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
    """Bram Ironwood, Human Fighter 3."""
    return Character(
        id="pc_bram",
        name="Bram Ironwood",
        is_pc=True,
        level=3,
        abilities={"STR": 16, "DEX": 12, "CON": 16, "INT": 10, "WIS": 12, "CHA": 10},
        max_hp=28,
        hp=28,
        ac=18,
        speed=30,
        skill_prof={"athletics": "prof", "perception": "prof", "intimidation": "prof"},
        save_prof=["STR", "CON"],
        portrait="盾",
        blurb="穩健的劍盾戰士。拿不定主意時，先揮劍就對了。",
        inventory=["長劍", "重弩與弩矢", "鏈甲與盾牌", "火把數支", "繩索 15 公尺", "乾糧與水袋", "少許銀幣"],
        actions=[
            Action(
                name="Longsword",
                type=ActionType.MELEE_ATTACK,
                to_hit=5,
                damage=[Damage(1, 8, 3, "slashing")],
                range_ft=5,
                description="可靠的近戰斬擊。",
            ),
            Action(
                name="Heavy Crossbow",
                type=ActionType.RANGED_ATTACK,
                to_hit=3,
                damage=[Damage(1, 10, 1, "piercing")],
                range_ft=100,
                description="用來對付遠處敵人的遠程射擊。",
            ),
            Action(
                name="Second Wind",
                type=ActionType.HEAL,
                cost=ActionCost.BONUS,
                heal=Damage(1, 10, 3, "healing"),
                uses=1,
                range_ft=0,
                description="重整呼吸，恢復一些生命值（每場戰鬥一次）。",
            ),
        ],
    )


def _lyra() -> Character:
    """Lyra Dawnbringer, Half-Elf Cleric 3 (Life)."""
    return Character(
        id="pc_lyra",
        name="Lyra Dawnbringer",
        is_pc=True,
        level=3,
        abilities={"STR": 12, "DEX": 10, "CON": 14, "INT": 10, "WIS": 16, "CHA": 12},
        max_hp=24,
        hp=24,
        ac=16,
        speed=30,
        skill_prof={"medicine": "prof", "perception": "prof", "diplomacy": "prof", "religion": "prof"},
        save_prof=["WIS", "CHA"],
        portrait="光",
        blurb="心地溫暖的牧師，能扶起同伴，也能懲戒邪惡。",
        inventory=["釘頭錘", "鎖子甲", "聖徽", "治療藥草", "火把數支", "祈禱書", "少許銀幣"],
        actions=[
            Action(
                name="Mace",
                type=ActionType.MELEE_ATTACK,
                to_hit=3,
                damage=[Damage(1, 6, 1, "bludgeoning")],
                range_ft=5,
                description="敵人逼近時使用的簡單鈍擊。",
            ),
            Action(
                name="Sacred Flame",
                type=ActionType.SAVE_SPELL,
                save_ability=Ability.DEX,
                save_dc=13,
                save_effect="none",
                damage=[Damage(1, 8, 0, "radiant")],
                range_ft=60,
                description="光輝聖火降下；目標需通過敏捷豁免，否則受到 1d8 傷害（隨意戲法）。",
            ),
            Action(
                name="Guiding Bolt",
                type=ActionType.RANGED_ATTACK,
                to_hit=5,
                damage=[Damage(4, 6, 0, "radiant")],
                uses=3,
                range_ft=120,
                description="一道光矛；法術攻擊命中後造成 4d6 光耀傷害（施放次數有限）。",
            ),
            Action(
                name="Cure Wounds",
                type=ActionType.HEAL,
                heal=Damage(1, 8, 3, "healing"),
                uses=4,
                range_ft=5,
                description="觸碰盟友，恢復 1d8+3 生命值（施放次數有限）。",
            ),
            Action(
                name="Healing Word",
                type=ActionType.HEAL,
                cost=ActionCost.BONUS,
                heal=Damage(1, 4, 3, "healing"),
                uses=2,
                range_ft=60,
                description="以附贈動作用遠距離治癒話語恢復盟友（施放次數有限）。",
            ),
        ],
    )


def premade_pcs() -> list[Character]:
    """Fresh copies of the two pre-made PCs."""
    return [_bram(), _lyra()]
