"""Two pre-made level-3 PCs in full PF2e build (character_format_v1.0.md).

Bram is a martial (Fighter), Lyra a divine caster (Cleric). Their numbers follow the
hybrid proficiency model (design §4.2): derived bonuses = ability mod + rank_bonus,
where rank_bonus layers PF2e's five ranks on the 5e bounded base PB = 2 + ⌊(level-1)/4⌋.

Action `cost` still uses the 5e action/bonus split because the combat engine has not yet
been converted to PF2e's three-action economy (a separate, larger change); spell content
and the rest of the build are PF2e.
"""
from __future__ import annotations

from ..engine.types import (
    Action,
    ActionCost,
    ActionType,
    Character,
    Damage,
)


def _bram() -> Character:
    """Bram Ironwood, Human Fighter 3 (PF2e build)."""
    return Character(
        id="pc_bram",
        name="Bram Ironwood",
        is_pc=True,
        level=3,
        abilities={"STR": 18, "DEX": 14, "CON": 14, "INT": 10, "WIS": 12, "CHA": 10},
        max_hp=44,
        hp=44,
        ac=21,
        speed=25,
        # origin
        ancestry="human",
        heritage="versatile_heritage",
        background="warrior",
        size="medium",
        traits=["human", "humanoid"],
        languages=["common"],
        # class
        class_="fighter",
        key_ability="STR",
        class_hp=10,
        class_features=["attack_of_opportunity", "bravery", "fighter_weapon_mastery"],
        # proficiencies (all five-step ranks)
        perception_prof="expert",
        save_prof={"fortitude": "expert", "reflex": "expert", "will": "trained"},
        skill_prof={"athletics": "expert", "intimidation": "trained", "perception": "expert"},
        lore_prof={"warfare": "trained"},
        attack_prof={"unarmed": "expert", "simple": "expert", "martial": "expert", "advanced": "trained"},
        defense_prof={"unarmored": "trained", "light": "trained", "medium": "trained", "heavy": "trained"},
        class_dc_prof="trained",
        spell_prof="untrained",
        feats=[
            {"name": "Power Attack", "type": "class", "level": 1, "note": "兩動作換一次加骰重擊"},
            {"name": "Natural Ambition", "type": "ancestry", "level": 1, "note": "額外 1 級職業專長"},
            {"name": "Intimidating Glare", "type": "skill", "level": 2, "note": "可用瞪視威懾"},
            {"name": "Toughness", "type": "general", "level": 3, "note": "增加 HP 與瀕死回復"},
        ],
        hero_points=1,
        guild_rank="F",
        portrait="盾",
        blurb="穩健的劍盾戰士。拿不定主意時，先揮劍就對了。",
        inventory=["長劍", "重弩與弩矢", "鏈甲與盾牌", "火把數支", "繩索 15 公尺", "乾糧與水袋", "少許銀幣"],
        actions=[
            Action(
                name="Longsword",
                type=ActionType.MELEE_ATTACK,
                to_hit=9,                       # STR +4, martial expert +4, +1 potency
                damage=[Damage(1, 8, 4, "slashing")],
                range_ft=5,
                description="可靠的近戰斬擊（+1 長劍）。",
            ),
            Action(
                name="Heavy Crossbow",
                type=ActionType.RANGED_ATTACK,
                to_hit=6,                       # DEX +2, martial expert +4
                damage=[Damage(1, 10, 0, "piercing")],
                range_ft=120,
                description="用來對付遠處敵人的遠程射擊。",
            ),
            Action(
                name="Catch Breath",
                type=ActionType.HEAL,
                cost=ActionCost.BONUS,
                heal=Damage(1, 10, 3, "healing"),
                uses=1,
                range_ft=0,
                description="重整呼吸、咬牙撐住，恢復一些生命值（每場戰鬥一次）。",
            ),
        ],
    )


def _lyra() -> Character:
    """Lyra Dawnbringer, Human (Half-Elf) Cleric 3 — divine, life-giving doctrine."""
    return Character(
        id="pc_lyra",
        name="Lyra Dawnbringer",
        is_pc=True,
        level=3,
        abilities={"STR": 10, "DEX": 12, "CON": 12, "INT": 10, "WIS": 18, "CHA": 14},
        max_hp=35,
        hp=35,
        ac=18,
        speed=25,
        # origin
        ancestry="human",
        heritage="half_elf",
        background="acolyte",
        size="medium",
        traits=["human", "half-elf", "humanoid"],
        languages=["common", "celestial", "elven"],
        senses=["low-light"],
        # class
        class_="cleric",
        key_ability="WIS",
        class_hp=8,
        class_features=["divine_font_heal", "doctrine_cloistered_cleric", "deity_lifegiver"],
        subclass="cloistered_cleric",
        # proficiencies
        perception_prof="trained",
        save_prof={"fortitude": "trained", "reflex": "trained", "will": "expert"},
        skill_prof={"medicine": "trained", "diplomacy": "trained", "religion": "trained", "perception": "trained"},
        lore_prof={"healing": "trained"},
        attack_prof={"unarmed": "trained", "simple": "trained", "martial": "untrained", "advanced": "untrained"},
        defense_prof={"unarmored": "trained", "light": "trained", "medium": "untrained", "heavy": "untrained"},
        class_dc_prof="trained",
        spell_prof="trained",
        feats=[
            {"name": "Healing Hands", "type": "class", "level": 1, "note": "Heal 治療骰升階為 d10"},
            {"name": "Cooperative Nature", "type": "ancestry", "level": 1, "note": "協助檢定加值"},
            {"name": "Battle Medicine", "type": "skill", "level": 2, "note": "戰鬥中以醫療技能急救"},
            {"name": "Diehard", "type": "general", "level": 3, "note": "瀕死 5 才死亡"},
        ],
        spellcasting={
            "tradition": "divine",
            "casting_type": "prepared",
            "ability": "WIS",
            "cantrips": ["divine_lance", "light", "stabilize", "guidance"],
            "cantrip_rank": 2,
            "slots": {"rank_1": 3, "rank_2": 2},
            "slots_used": {"rank_1": 0, "rank_2": 0},
            "prepared": {
                "rank_1": ["heal", "bless", "fear"],
                "rank_2": ["heal", "spiritual_armament"],
            },
            "focus_points": 1,
            "focus_spells": ["healers_blessing"],
        },
        hero_points=1,
        guild_rank="F",
        portrait="光",
        blurb="心地溫暖的牧師，能扶起同伴，也能懲戒邪惡。",
        inventory=["釘頭錘", "鎖子甲", "聖徽", "治療藥草", "火把數支", "祈禱書", "少許銀幣"],
        actions=[
            Action(
                name="Mace",
                type=ActionType.MELEE_ATTACK,
                to_hit=2,                       # STR +0, simple trained +2
                damage=[Damage(1, 6, 0, "bludgeoning")],
                range_ft=5,
                description="敵人逼近時使用的簡單鈍擊。",
            ),
            Action(
                name="Divine Lance",
                type=ActionType.RANGED_ATTACK,
                to_hit=6,                       # spell attack: WIS +4, spell trained +2
                damage=[Damage(1, 4, 4, "spirit")],
                range_ft=30,
                description="擲出一道神聖光矛；法術攻擊命中造成靈性傷害（戲法，可隨意施放）。",
            ),
            Action(
                name="Heal",
                type=ActionType.HEAL,
                heal=Damage(1, 8, 8, "healing"),  # 2-action Heal, +8 from range form
                uses=3,
                range_ft=30,
                description="引導正能量療癒盟友，恢復 1d8+8 生命值（消耗法術位）。",
            ),
            Action(
                name="Healing Font",
                type=ActionType.HEAL,
                cost=ActionCost.BONUS,
                heal=Damage(1, 8, 0, "healing"),
                uses=3,
                range_ft=30,
                description="以神術之泉的額外治療法術迅速為盟友回復（消耗治療之泉）。",
            ),
        ],
    )


def premade_pcs() -> list[Character]:
    """Fresh copies of the two pre-made PCs."""
    return [_bram(), _lyra()]
