"""Monster templates for the one-shot and entity-backed NPC combatants."""
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


def _commoner() -> Character:
    return Character(
        id="commoner",
        name="Commoner",
        is_pc=False,
        level=1,
        abilities={"STR": 10, "DEX": 10, "CON": 10, "INT": 10, "WIS": 10, "CHA": 10},
        max_hp=4,
        hp=4,
        ac=10,
        speed=30,
        portrait="民",
        blurb="臨時捲入衝突的普通人，只有求生本能和手邊器物。",
        actions=[
            Action("Improvised Strike", ActionType.MELEE_ATTACK, to_hit=2, damage=[Damage(1, 4, 0, "bludgeoning")], range_ft=5),
        ],
    )


def _thug() -> Character:
    return Character(
        id="thug",
        name="Thug",
        is_pc=False,
        level=1,
        abilities={"STR": 15, "DEX": 11, "CON": 14, "INT": 10, "WIS": 10, "CHA": 11},
        max_hp=16,
        hp=16,
        ac=11,
        speed=30,
        portrait="棍",
        blurb="慣於街頭鬥毆的暴力分子，靠蠻力和狠勁壓人。",
        actions=[
            Action("Heavy Club", ActionType.MELEE_ATTACK, to_hit=4, damage=[Damage(1, 6, 2, "bludgeoning")], range_ft=5),
            Action("Thrown Knife", ActionType.RANGED_ATTACK, to_hit=2, damage=[Damage(1, 4, 0, "piercing")], range_ft=20),
        ],
    )


def _guard() -> Character:
    return Character(
        id="guard",
        name="Guard",
        is_pc=False,
        level=1,
        abilities={"STR": 13, "DEX": 12, "CON": 12, "INT": 10, "WIS": 11, "CHA": 10},
        max_hp=11,
        hp=11,
        ac=16,
        speed=30,
        portrait="盾",
        blurb="受過基本武裝訓練的守衛，懂得用盾與長兵器保持距離。",
        actions=[
            Action("Spear", ActionType.MELEE_ATTACK, to_hit=3, damage=[Damage(1, 6, 1, "piercing")], range_ft=5),
            Action("Light Crossbow", ActionType.RANGED_ATTACK, to_hit=3, damage=[Damage(1, 8, 1, "piercing")], range_ft=80),
        ],
    )


def _beast() -> Character:
    return Character(
        id="beast",
        name="Beast",
        is_pc=False,
        level=1,
        abilities={"STR": 12, "DEX": 14, "CON": 12, "INT": 3, "WIS": 12, "CHA": 6},
        max_hp=11,
        hp=11,
        ac=13,
        speed=40,
        portrait="獸",
        blurb="被驚動或受驅使的野獸，以牙爪和速度撕開破綻。",
        actions=[
            Action("Bite", ActionType.MELEE_ATTACK, to_hit=4, damage=[Damage(1, 6, 2, "piercing")], range_ft=5),
            Action("Claw", ActionType.MELEE_ATTACK, to_hit=4, damage=[Damage(1, 4, 2, "slashing")], range_ft=5),
        ],
    )


_ARCHETYPES = {
    "commoner": _commoner,
    "thug": _thug,
    "guard": _guard,
    "beast": _beast,
}

_GUARD_WORDS = (
    "guard", "soldier", "watch", "militia", "sentry", "mercenary",
    "衛兵", "守衛", "士兵", "哨兵", "民兵", "傭兵", "護衛",
)
_THUG_WORDS = (
    "thug", "bandit", "ruffian", "brute", "raider", "cutthroat",
    "惡棍", "打手", "強盜", "土匪", "暴徒", "掠奪者", "混混",
)
_BEAST_WORDS = (
    "beast", "wolf", "dog", "bear", "boar", "rat", "creature", "animal",
    "野獸", "狼", "犬", "狗", "熊", "野豬", "鼠", "怪物", "動物",
)
_ELITE_WORDS = (
    "boss", "captain", "chief", "leader", "elite", "veteran",
    "首領", "隊長", "頭目", "精英", "老練", "菁英",
)
# Humanoid (person) cues used only when a free-text combat target carries no
# registry record — to decide person vs creature for a materialised combatant.
_PERSON_WORDS = (
    "酒保", "老闆", "掌櫃", "商人", "村民", "農夫", "侍者", "店主", "旅人", "祭司",
    "貴族", "男人", "女人", "老人", "孩子", "女子", "男子", "婦人", "和尚", "法師",
    "刺客", "盜賊", "弓手", "船長", "領主", "管家",
    "bartender", "innkeeper", "merchant", "villager", "priest", "noble", "man",
    "woman", "thief", "assassin", "archer", "captain", "lord", "wizard",
)


def _entity_text(entity: dict) -> str:
    aliases = " ".join(str(a) for a in (entity.get("aliases") or []))
    return " ".join(str(x or "") for x in (
        entity.get("kind"), entity.get("name"), aliases, entity.get("notes"),
    )).lower()


def archetype_for_entity(entity: dict) -> str:
    """Pick the closest combat archetype for a narrative entity."""
    text = _entity_text(entity)
    if entity.get("kind") == "creature" or any(word in text for word in _BEAST_WORDS):
        return "beast"
    if any(word in text for word in _GUARD_WORDS):
        return "guard"
    if any(word in text for word in _THUG_WORDS):
        return "thug"
    return "commoner"


# Disposition cues for a materialised attack target with no registry record. Obviously
# malicious things start `hostile` (combat begins on the first swing); plainly peaceful
# folk start `friendly` (they tolerate two swings before fighting back); everything else
# is `neutral`. Reuses the thug list — a "bandit" reads as hostile, not just a brawler type.
_HOSTILE_WORDS = _THUG_WORDS + (
    "哥布林", "狗頭人", "怪物", "魔物", "巨魔", "食人魔", "不死", "亡靈", "骷髏", "殭屍",
    "喪屍", "惡魔", "魔王", "刺客", "盜賊", "海盜", "野蠻人",
    "goblin", "kobold", "monster", "troll", "ogre", "undead", "skeleton", "zombie",
    "demon", "fiend", "assassin", "pirate", "barbarian",
)
_PEACEFUL_WORDS = (
    "酒保", "老闆", "掌櫃", "商人", "村民", "農夫", "侍者", "店主", "旅人", "婦人",
    "孩子", "老人", "女子", "男子", "祭司", "僧侶", "和尚", "管家",
    "bartender", "innkeeper", "merchant", "villager", "priest", "monk", "child",
)


def infer_disposition(text: str) -> str:
    """Guess the starting attitude of a materialised, unregistered attack target. Drives the
    provocation ladder: hostile → combat now; friendly → tolerates two swings; neutral → one."""
    low = (text or "").lower()
    if any(word in low for word in _HOSTILE_WORDS):
        return "hostile"
    if any(word in low for word in _PEACEFUL_WORDS):
        return "friendly"
    return "neutral"


def infer_combat_kind(text: str) -> str:
    """Guess whether a free-text combat target is a `person` (humanoid) or a
    `creature` (beast/monster). Used when an enemy materialises mid-play and no
    registry record exists to read `kind` from. Beast cues win first (a "wolf"
    is a creature even if the sentence mentions a person); explicit humanoid cues
    map to `person`; everything else defaults to `creature` (most narrated foes in
    this game are monsters, and `creature` maps cleanly to the beast archetype)."""
    low = (text or "").lower()
    if any(word in low for word in _BEAST_WORDS):
        return "creature"
    if any(word in low for word in _PERSON_WORDS + _GUARD_WORDS + _THUG_WORDS):
        return "person"
    return "creature"


def _elite_adjustment(character: Character, entity: dict) -> Character:
    text = _entity_text(entity)
    if not any(word in text for word in _ELITE_WORDS):
        return character
    character.level = max(character.level, 2)
    character.max_hp += 6
    character.hp = character.max_hp
    character.ac += 1
    for action in character.actions:
        action.to_hit += 1
        for dmg in action.damage:
            dmg.bonus += 1
    return character


def spawn(key: str, index: int = 1) -> Character:
    """Create a uniquely-id'd monster from a template, numbered when several appear."""
    template = _TEMPLATES[key]()
    if key == "goblin_boss":
        template.id = key
        return template
    template.id = f"{key}_{index}"
    template.name = f"{template.name} {index}"
    return template


def spawn_from_entity(entity: dict) -> Character:
    """Create a combat Character from a narrative entity, preserving entity id."""
    archetype = archetype_for_entity(entity)
    template = _ARCHETYPES[archetype]()
    name = str(entity.get("name") or template.name)
    spawned = template.clone(id=str(entity["id"]), name=name)
    return _elite_adjustment(spawned, entity)
