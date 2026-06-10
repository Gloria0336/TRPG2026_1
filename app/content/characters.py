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


def _ann() -> Character:
    """ann, Half-Elf Sorcerer 3 — Fey bloodline, underground operative."""
    return Character(
        id="pc_ann",
        name="Ann",
        is_pc=True,
        level=3,
        abilities={"STR": 8, "DEX": 16, "CON": 14, "INT": 10, "WIS": 12, "CHA": 18},
        max_hp=32,
        hp=32,
        ac=16,
        speed=30,
        ancestry="human",
        heritage="half_elf",
        background="underground_operative",
        size="medium",
        traits=["human", "half-elf", "humanoid"],
        languages=["common", "sylvan"],
        senses=["low-light"],
        class_="sorcerer",
        key_ability="CHA",
        class_hp=6,
        class_features=[
            "fey_bloodline",
            "primal_tradition",
            "spontaneous_spellcasting",
        ],
        subclass="fey_bloodline",
        perception_prof="trained",
        save_prof={"fortitude": "trained", "reflex": "trained", "will": "expert"},
        skill_prof={
            "stealth": "expert",
            "deception": "trained",
            "society": "trained",
            "occultism": "trained",
            "perception": "trained",
        },
        lore_prof={"underworld": "trained"},
        attack_prof={"unarmed": "trained", "simple": "trained", "martial": "untrained", "advanced": "untrained"},
        defense_prof={"unarmored": "trained", "light": "trained", "medium": "untrained", "heavy": "untrained"},
        class_dc_prof="trained",
        spell_prof="trained",
        feats=[
            {
                "name": "Dangerous Sorcery",
                "type": "class",
                "level": 1,
                "note": "傷害法術額外造成等同法術等級的傷害（已算入各 Action 的 modifier）",
            },
            {
                "name": "Nimble Elf",
                "type": "ancestry",
                "level": 1,
                "note": "繼承精靈步伐，移速 +5 尺（30 尺總計）",
            },
            {
                "name": "Streetwise",
                "type": "skill",
                "level": 2,
                "note": "以社會技能查黑市行情、辨識地下勢力與線人",
            },
            {
                "name": "Incredible Initiative",
                "type": "general",
                "level": 3,
                "note": "先攻骰 +2；幾乎永遠先手",
            },
        ],
        spellcasting={
            "tradition": "primal",
            "casting_type": "spontaneous",
            "ability": "CHA",
            "cantrips": [
                "electric_arc",
                "tanglefoot",
                "shield",
                "prestidigitation",
                "detect_magic",
            ],
            "cantrip_rank": 2,
            "slots": {"rank_1": 4, "rank_2": 3},
            "slots_used": {"rank_1": 0, "rank_2": 0},
            "spells_known": {
                "rank_1": ["magic_missile", "fear", "charm"],
                "rank_2": ["invisibility", "glitterdust"],
            },
            "signature_spells": ["magic_missile", "fear", "invisibility"],
            "focus_points": 1,
            "focus_spells": ["faerie_fire"],
        },
        hero_points=1,
        guild_rank="F",
        portrait="影",
        blurb="沒有姓，沒有過去，檔案欄填「—」。接單、出手、消失，不解釋。",
        inventory=[
            "短劍",
            "皮甲",
            "手弩與弩矢×20",
            "煙霧彈×2",
            "暗語紙條",
            "空白委任狀",
            "少許銀幣",
        ],
        actions=[
            Action(
                name="Short Sword",
                type=ActionType.MELEE_ATTACK,
                to_hit=5,
                damage=[Damage(1, 6, 3, "piercing")],
                range_ft=5,
                description="被逼到近身時的最後手段，快而精準。",
            ),
            Action(
                name="Electric Arc",
                type=ActionType.RANGED_ATTACK,
                to_hit=6,
                damage=[Damage(2, 4, 6, "electricity")],
                range_ft=30,
                description="Reflex DC 16；可同時電擊最多兩個目標（戲法，無限施放）。",
            ),
            Action(
                name="Magic Missile",
                type=ActionType.RANGED_ATTACK,
                to_hit=99,
                damage=[Damage(1, 4, 2, "force")],
                range_ft=120,
                uses=4,
                description="自動命中力場飛彈；每追加1動作增加1枚；消耗1階法術位。",
            ),
            Action(
                name="Fear",
                type=ActionType.RANGED_ATTACK,
                to_hit=6,
                damage=[Damage(0, 0, 0, "fear")],
                range_ft=30,
                uses=4,
                description="Will DC 16；失敗→驚懼2，成功→驚懼1；消耗1階法術位。",
            ),
            Action(
                name="Invisibility",
                type=ActionType.HEAL,
                cost=ActionCost.BONUS,
                heal=Damage(0, 0, 0, "buff"),
                uses=3,
                range_ft=30,
                description="對自身或友方施放隱形，持續10分鐘；攻擊或施法後解除；消耗2階法術位。",
            ),
            Action(
                name="Faerie Fire",
                type=ActionType.RANGED_ATTACK,
                to_hit=6,
                damage=[Damage(0, 0, 0, "revelation")],
                range_ft=60,
                uses=1,
                description="【專注法術】照亮10尺範圍，揭示隱形生物並使目標眩目（Dazzled）；消耗1專注點。",
            ),
        ],
    )


def _vael() -> Character:
    """Vael Ashcroft（瓦爾.艾許.克羅芙）, Elf Rogue 3 — Thief Racket（PF2e build）。"""
    return Character(
        id="pc_vael",
        name="Vael Ashcroft",
        is_pc=True,
        level=3,
        abilities={"STR": 10, "DEX": 18, "CON": 10, "INT": 14, "WIS": 10, "CHA": 14},
        max_hp=33,                              # 精靈 6 +（Rogue 8 + CON 0）×3 + Toughness 3
        hp=33,
        ac=18,                                  # 無甲 Expert：10 + DEX 4 + rank 4
        speed=30,                               # 精靈基礎移速 30 ft
        # origin
        ancestry="elf",
        heritage="whisper_elf",
        background="noble",
        size="medium",
        traits=["elf", "humanoid"],
        languages=["common", "elven", "draconic"],   # 龍語——古代魔法文獻的通用語
        senses=["low-light"],
        # class
        class_="rogue",
        key_ability="DEX",
        class_hp=8,
        class_features=[
            "sneak_attack_2d6",                 # 側翼或措手不及時觸發
            "racket_thief",                     # DEX 取代 STR 計算精巧武器傷害
            "nimble_dodge",                     # 反應動作：被攻擊時 AC +2
            "deny_advantage",                   # 等級 3：無法被側翼偷襲
        ],
        # proficiencies
        perception_prof="expert",
        save_prof={"fortitude": "trained", "reflex": "expert", "will": "trained"},
        skill_prof={
            "stealth": "expert",
            "acrobatics": "expert",
            "deception": "trained",
            "society": "trained",
            "thievery": "trained",
            "perception": "expert",
        },
        lore_prof={"forbidden_texts": "trained", "nobility": "trained"},
        attack_prof={"unarmed": "trained", "simple": "expert", "martial": "trained", "advanced": "untrained"},
        defense_prof={"unarmored": "expert", "light": "trained", "medium": "untrained", "heavy": "untrained"},
        class_dc_prof="trained",
        spell_prof="untrained",
        feats=[
            {"name": "Twin Feint", "type": "class", "level": 1, "note": "兩動作連招：第一刃佯攻，第二刃必然觸發 Sneak Attack"},
            {"name": "Forlorn", "type": "ancestry", "level": 1, "note": "久居人類社會的精靈；對哀愁與情感操控有抵抗力，Will 豁免情境加值"},
            {"name": "Quiet Allies", "type": "skill", "level": 2, "note": "帶領同伴潛行時取隊伍最高 Stealth，而非最低"},
            {"name": "Toughness", "type": "general", "level": 3, "note": "補足精靈脆弱體質；+HP 並改善瀕死回復"},
        ],
        hero_points=1,
        guild_rank="F",
        portrait="刃",
        blurb="瓦爾.艾許.克羅芙。貴族。學者。或者其他什麼。他們從不解釋——你若非聰明到自己看清，便不值得知道。",
        inventory=[
            "禁忌匕首（主要武器）",
            "匕首 ×3（投擲備用）",
            "精製盜賊工具",
            "昏睡毒素 ×2",
            "貴族紋徽（真品，偶爾用於偽造身份）",
            "禁忌手記（半完成的古代魔法研究筆記，以龍語書寫）",
            "50 金幣",
            "替換用平民衣物",
        ],
        actions=[
            Action(
                name="Dagger",
                type=ActionType.MELEE_ATTACK,
                to_hit=9,                       # DEX +4, simple expert +4, +1 potency rune
                damage=[Damage(1, 4, 4, "piercing")],   # Thief Racket：DEX 算傷害
                range_ft=5,
                description="禁忌匕首刺擊（+1 精製匕首）。側翼或敵人措手不及時額外觸發 Sneak Attack +2d6。",
            ),
            Action(
                name="Thrown Dagger",
                type=ActionType.RANGED_ATTACK,
                to_hit=9,                       # 同上，投擲用 DEX
                damage=[Damage(1, 4, 4, "piercing")],
                range_ft=20,
                description="投擲匕首。有效距離 4 格。精準無聲，不留明顯痕跡。",
            ),
            Action(
                name="Twin Feint",
                type=ActionType.MELEE_ATTACK,
                to_hit=9,
                damage=[
                    Damage(1, 4, 4, "piercing"),   # 第一刃（可能不中，但確保第二刃）
                    Damage(1, 4, 4, "piercing"),   # 第二刃（保證 Sneak Attack）
                    Damage(2, 6, 0, "piercing"),   # Sneak Attack
                ],
                range_ft=5,
                description=(
                    "【兩動作】第一刃虛招迷惑視線，第二刃無論對方是否措手不及都觸發 Sneak Attack。"
                    "裴玦的核心輸出手段。"
                ),
            ),
            Action(
                name="Nimble Dodge",
                type=ActionType.UTILITY,        # 引擎無 REACTION 型別，以 UTILITY + BONUS 表示反應
                cost=ActionCost.BONUS,
                range_ft=0,
                description="【反應】敵人宣告攻擊後閃身側移——本次攻擊對裴玦的命中 DC +2。不需要預判，只需要還活著。",
            ),
        ],
    )


def premade_pcs() -> list[Character]:
    """Fresh copies of the pre-made PCs."""
    return [_bram(), _lyra(), _vael(), _ann()]
