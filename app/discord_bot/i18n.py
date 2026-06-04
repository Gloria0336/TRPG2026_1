"""Traditional Chinese display helpers for Discord messages."""
from __future__ import annotations

import re

from ..engine.types import Character

NAME_ZH = {
    "Bram Ironwood": "布拉姆·鐵木",
    "Lyra Dawnbringer": "萊拉·曦光使者",
    "Goblin": "哥布林",
    "Grix the Goblin Boss": "哥布林首領葛利克斯",
    "Old Perrin": "老佩林",
    "Dawnbridge": "晨橋村",
    "The Dawnbridge Caravan": "晨橋商隊",
    "The Gilded Tankard": "鎏金酒杯酒館",
}

ABILITY_ZH = {
    "STR": "力量",
    "DEX": "敏捷",
    "CON": "體質",
    "INT": "智力",
    "WIS": "感知",
    "CHA": "魅力",
}

SKILL_ZH = {
    # PF2e skills (design §4.2)
    "acrobatics": "特技",
    "arcana": "奧秘",
    "athletics": "運動",
    "crafting": "製作",
    "deception": "欺瞞",
    "diplomacy": "交涉",
    "intimidation": "威嚇",
    "medicine": "醫療",
    "nature": "自然",
    "occultism": "神秘學",
    "perception": "察覺",
    "performance": "表演",
    "religion": "宗教",
    "society": "社會",
    "stealth": "隱匿",
    "survival": "求生",
    "thievery": "盜竊",
    # Legacy 5e aliases — keep so old event_log/snapshot text still renders.
    "animal handling": "馴獸",
    "animal_handling": "馴獸",
    "history": "歷史",
    "insight": "洞悉",
    "investigation": "調查",
    "persuasion": "說服",
    "sleight of hand": "巧手",
    "sleight_of_hand": "巧手",
}

ACTION_ZH = {
    "Longsword": "長劍",
    "Heavy Crossbow": "重弩",
    "Second Wind": "回氣",
    "Mace": "硬頭鎚",
    "Sacred Flame": "聖焰",
    "Guiding Bolt": "曳光彈",
    "Cure Wounds": "治療傷口",
    "Healing Word": "治療真言",
    "Scimitar": "彎刀",
    "Shortbow": "短弓",
    "Scimitar Flurry": "彎刀連擊",
    "Javelin": "標槍",
}

CONDITION_ZH = {
    "unconscious": "昏迷",
    "stable": "穩定",
    "dead": "死亡",
}

OUTCOME_ZH = {
    "victory": "勝利",
    "defeat": "敗北",
}


def name(value: str | None) -> str:
    raw = str(value or "")
    if raw in NAME_ZH:
        return NAME_ZH[raw]
    if raw.startswith("Goblin "):
        return raw.replace("Goblin ", "哥布林 ", 1)
    return raw


def action(value: str | None) -> str:
    return ACTION_ZH.get(str(value or ""), str(value or ""))


def skill(value: str | None) -> str:
    raw = str(value or "")
    return SKILL_ZH.get(raw.lower(), raw.replace("_", " ").title())


def condition(value: str | None) -> str:
    return CONDITION_ZH.get(str(value or ""), str(value or ""))


def outcome(value: str | None) -> str:
    return OUTCOME_ZH.get(str(value or ""), str(value or ""))


def character_label(c: Character) -> str:
    return f"{c.portrait} {name(c.name)}"


def _replace_words(text: str, replacements: dict[str, str]) -> str:
    for en, zh in replacements.items():
        text = re.sub(rf"\b{re.escape(en)}\b", zh, text)
    return text


def text(value: str | None) -> str:
    out = str(value or "")
    for en, zh in NAME_ZH.items():
        out = out.replace(en, zh)
    for en, zh in ACTION_ZH.items():
        out = out.replace(en, zh)
    for en, zh in OUTCOME_ZH.items():
        out = out.replace(en, zh)
    replacements = {
        "Scene 1 —": "場景 1：",
        "Scene 2 —": "場景 2：",
        "Scene 3 —": "場景 3：",
        "Scene 4 —": "場景 4：",
        "The East Road": "東路",
        "Ambush at the Warren": "巢穴前的伏擊",
        "The Warren (Climax)": "巢穴（高潮）",
        "Combat begins! Initiative:": "戰鬥開始！先攻順序：",
        "Combat ends": "戰鬥結束",
        "The party is victorious!": "隊伍獲得勝利！",
        "The party has fallen...": "隊伍倒下了...",
        "What would you like to do?": "你想做什麼？",
        "investigate it": "調查這件事",
        "look closer": "仔細觀察",
        "talk it out": "試著說服",
        "force the issue": "用力量突破",
        "Look around": "四處觀察",
        "Talk to someone": "找人交談",
        "Examine something": "仔細檢查某樣東西",
        "Move on": "繼續前進",
        "CRIT SUCCESS": "大成功",
        "CRIT FAILURE": "大失敗",
        "SUCCESS": "成功",
        "FAILURE": "失敗",
        "HIT": "命中",
        "MISS": "未命中",
        "SAVED": "豁免成功",
        "FAILED save": "豁免失敗",
        "critical!": "重擊！",
        "crit!": "大成功！",
        "fumble!": "大失敗！",
        "check vs DC": "檢定，DC",
        "has failed three death saves and dies": "死亡豁免累積三次失敗，死亡",
        "drops to 0 生命值 and falls unconscious": "生命值降到 0 並陷入昏迷",
        "death saves begin": "開始進行死亡豁免",
    }
    word_replacements = {
        "attacks": "攻擊",
        "with": "使用",
        "casts": "施放",
        "uses": "使用",
        "on": "目標",
        "takes": "受到",
        "damage": "點傷害",
        "heals": "恢復",
        "HP": "生命值",
        "AC": "護甲",
        "Roll": "擲骰",
        "Death save": "死亡豁免",
        "death save": "死亡豁免",
        "natural 20": "自然 20",
        "natural 1": "自然 1",
        "two failures": "兩次失敗",
        "success": "成功",
        "failure": "失敗",
        "auto-hit": "自動命中",
        "is unharmed": "毫髮無傷",
        "is defeated": "被擊敗",
        "regains": "恢復",
        "is back on their feet": "重新站起來",
        "is stable": "狀態穩定",
    }
    for en, zh in replacements.items():
        out = out.replace(en, zh)
    out = _replace_words(out, word_replacements)
    for en, zh in SKILL_ZH.items():
        out = out.replace(en.replace("_", " ").title(), zh)
        out = out.replace(en.title(), zh)
    if out.startswith("Goblin "):
        out = out.replace("Goblin ", "哥布林 ", 1)
    return out
