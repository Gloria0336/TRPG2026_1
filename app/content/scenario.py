"""The short one-shot: "The Dawnbridge Caravan".

A short 4-scene adventure for two PCs designed to onboard TRPG newcomers.
"""
from __future__ import annotations

# Title shown by /start and the dashboard.
TITLE = "晨橋商隊"

INTRO = (
    "**晨橋商隊**，一段適合兩人的短篇冒險。\n\n"
    "邊境村莊**晨橋村**坐落在東路跨越湍急河川之處。三天前，一支商隊沿東路出發，"
    "卻始終沒有抵達下一座城鎮。今晚，在鎏金酒杯酒館溫暖的燈光下，有人正準備開口求助。"
)

HOW_TO_PLAY = (
    "**玩法說明**：只要在這個頻道用白話說出你的角色要做什麼。\n"
    "例如：問酒保失蹤商隊的事。我會判斷這需要哪一種技能檢定。\n"
    "如果需要擲骰，我會貼出一個擲骰按鈕，點擊後就會顯示結果。骰子會在伺服器端擲出，"
    "按鈕只負責公開結果。\n"
    "不知道該做什麼也沒關係，說個大方向，我會提供可行選項。\n"
    "斜線指令：`/character`（角色卡）、`/scene`（目前場景）、`/roll 1d20+3`（手動擲骰）。"
)

# Authored locations, seeded once into the GLOBAL location registry at new-game (design
# §6: location is first-class state). Ids match the authored SCENES so natural-language
# travel resolves to canonical places — and traveling to one that is also a scripted
# scene re-enters that scene with its full content (challenges/encounter/entities).
# Emergent places the players invent are auto-registered by store.resolve_or_register_location.
LOCATIONS: list[dict] = [
    {"id": "tavern", "name": "鎏金酒杯酒館", "aliases": ["酒館", "鎏金酒杯", "鎏金酒杯酒館"],
     "notes": "晨橋村熱鬧的酒館，冒險的起點。"},
    {"id": "east_road", "name": "東路", "aliases": ["東邊道路", "大路", "東面道路"],
     "notes": "晨橋村往東的道路，商隊失蹤之處。"},
    {"id": "warren", "name": "哥布林巢穴", "aliases": ["巢穴", "哥布林窩", "山坡裂隙"],
     "notes": "山坡裂隙後、煙霧瀰漫的哥布林巢穴。"},
]

# Per-scene cost pools (design §4.7). When a check lands in PARTIAL or FAILURE the
# engine picks a CostType from this list; severity is picked by band. Each entry is
# the string value of a CostType enum; the engine cycles through them deterministically
# (via the seeded RNG) so testing stays reproducible. Order is descriptive priority.
SCENES: list[dict] = [
    {
        "id": "tavern",
        "title": "場景 1：鎏金酒杯酒館",
        "summary": (
            "隊伍坐在晨橋村熱鬧的鎏金酒杯酒館裡。焦急的商人老佩林上前求助："
            "他的商隊三天前在東路上失蹤，民兵又不願搜救。只要有人能找回商隊，"
            "他願意支付 50 枚金幣。角落裡，一名緊張的兜帽客不時偷瞄佩林。"
        ),
        "npcs": ["老佩林（商人）", "緊張的兜帽客"],
        # Narrative entities seeded into the DB on scene entry (people/objects with
        # state markers). The narrator reads these — not the static summary — so
        # presence/disposition stay consistent across turns.
        "entities": [
            {
                "id": "ent_perrin", "kind": "person", "name": "老佩林",
                "aliases": ["佩林", "老佩林（商人）", "商人"],
                "status": "present", "disposition": "friendly",
                "notes": "焦急的商人；商隊三天前在東路失蹤，懸賞 50 金幣。",
            },
            {
                "id": "ent_hooded", "kind": "person", "name": "緊張的兜帽客",
                "aliases": ["兜帽客", "兜帽人", "神秘人", "兜帽客人"],
                "status": "present", "disposition": "afraid",
                "notes": "坐在角落，不時偷瞄佩林。",
            },
        ],
        "challenges": {
            "persuasion": 13,
            "insight": 12,
            "perception": 15,
            "intimidation": 15,
        },
        "cost_pool": ["relation", "attention", "time"],
        "onboarding": [
            "請老佩林喝一杯，問清楚商隊原本要去哪裡。（說服/洞悉）",
            "掃視房間，看看有沒有人在偷聽。（察覺）",
            "接受委託。（推進故事）",
        ],
        "encounter": None,
        "advance_hint": "當隊伍接受委託並蒐集完線索後，前往東路。",
    },
    {
        "id": "east_road",
        "title": "場景 2：東路",
        "summary": (
            "清晨的薄霧貼著森林，東路在此逐漸變窄。走了幾百步後，隊伍找到了商隊："
            "兩輛翻覆的貨車、散落的箱子、深色血跡，卻幾乎沒有屍體。粗糙的足跡離開道路，"
            "通往一處岩石山坡。前方小徑上，有一條絆線微微反光。"
        ),
        "npcs": [],
        "entities": [
            {
                "id": "obj_wagons", "kind": "object", "name": "翻覆的貨車",
                "aliases": ["貨車", "翻覆貨車", "馬車"], "status": "present",
                "notes": "兩輛翻覆的貨車、散落的箱子、深色血跡，幾乎沒有屍體。",
            },
            {
                "id": "obj_tracks", "kind": "object", "name": "粗糙的足跡",
                "aliases": ["足跡", "腳印", "痕跡"], "status": "present",
                "notes": "離開道路、通往岩石山坡的足跡。",
            },
            {
                "id": "obj_tripwire", "kind": "object", "name": "絆線",
                "aliases": ["絆線", "陷阱", "反光的線"], "status": "present",
                "notes": "前方小徑上微微反光的絆線。",
            },
        ],
        "challenges": {
            "investigation": 12,
            "survival": 13,
            "perception": 13,
            "acrobatics": 12,
        },
        "cost_pool": ["time", "trace", "resource"],
        "onboarding": [
            "搜索翻覆貨車，尋找線索。（調查）",
            "尋找離開道路的足跡。（求生）",
            "檢查前方小徑是否有陷阱。（察覺）",
        ],
        "encounter": None,
        "advance_hint": "沿著足跡前進，會抵達哥布林巢穴入口，並遭遇伏擊。",
    },
    {
        "id": "ambush",
        "title": "場景 3：巢穴前的伏擊",
        "summary": (
            "足跡止於山坡裂隙前，裡頭飄出木煙與哥布林的臭味。隊伍靠近時，三隻哥布林"
            "從灌木叢衝出，揮舞生鏽彎刀，尖叫著發出戰吼。擲先攻！"
        ),
        "npcs": [],
        "entities": [
            {
                "id": "loc_warren_mouth", "kind": "location", "name": "山坡裂隙",
                "aliases": ["裂隙", "巢穴入口", "洞口"], "status": "present",
                "notes": "飄出木煙與哥布林臭味的巢穴入口。",
            },
        ],
        "challenges": {},
        "cost_pool": ["resource", "exposure"],
        "onboarding": [
            "輪到你時，可以說：用長劍攻擊一隻哥布林。",
            "萊拉可以施放聖焰或曳光彈，也可以用治療傷口替同伴恢復。",
            "每次攻擊我都會貼出擲骰按鈕。",
        ],
        "encounter": [("goblin", 3)],
        "advance_hint": "擊倒伏擊者後，隊伍可以深入巢穴追查首領。",
    },
    {
        "id": "warren",
        "title": "場景 4：巢穴（高潮）",
        "summary": (
            "煙霧瀰漫的巢穴深處，哥布林首領葛利克斯用刀抵著一名被俘商隊護衛，"
            "身旁還站著一名哥布林副手。葛利克斯冷笑道：「現在離開，不然人質就死！」"
            "隊伍可以開戰、說服葛利克斯退讓，或嘗試偷偷救出人質。"
        ),
        "npcs": ["哥布林首領葛利克斯", "被俘商隊護衛"],
        "entities": [
            {
                "id": "ent_grix", "kind": "creature", "name": "哥布林首領葛利克斯",
                "aliases": ["葛利克斯", "首領", "哥布林首領"], "status": "present",
                "disposition": "hostile",
                "notes": "用刀挾持人質，威脅隊伍離開。",
            },
            {
                "id": "ent_hostage", "kind": "person", "name": "被俘商隊護衛",
                "aliases": ["護衛", "人質", "商隊護衛"], "status": "present",
                "disposition": "afraid",
                "notes": "被葛利克斯用刀抵住的人質。",
            },
        ],
        "challenges": {
            "persuasion": 15,
            "intimidation": 15,
            "stealth": 15,
            "deception": 15,
        },
        "cost_pool": ["exposure", "attention", "relation"],
        "onboarding": [
            "戰鬥：衝向葛利克斯！（開始與首領和副手交戰）",
            "談判：試著說服葛利克斯拿走金幣，放走護衛。（說服）",
            "潛行：溜進陰影中救出人質。（隱匿）",
        ],
        "encounter": [("goblin_boss", 1), ("goblin", 1)],
        "advance_hint": "不論結局如何，商隊與人質的命運已在此刻寫定。",
    },
]

# Story goals — the SOFT spine (design §7.3) that replaces the rigid scene index. Each
# beat completes on a structured world signal: a flag set by play, or the party having
# reached a place (`done_if_reached`). Players may reach beats out of order or skip them
# entirely; the director (app/content/director.py) just tracks state and nudges — it
# never drags the party back to a scripted scene. `terminal` marks the climax beat whose
# completion ends the one-shot.
GOALS: list[dict] = [
    {
        "id": "accept_quest",
        "title": "接下晨橋商隊的委託",
        "nudge": "老佩林還在酒館裡等著答覆——和他談談失蹤的商隊，或動身前往東路。",
        "done_flags": ["accepted_quest"],
        "done_if_reached": ["east_road", "ambush", "warren"],
    },
    {
        "id": "find_caravan",
        "title": "查出商隊的下落",
        "nudge": "東路上有翻覆的貨車與離開道路的足跡，循著線索追查下去。",
        "done_flags": ["found_caravan"],
        "done_if_reached": ["ambush", "warren"],
    },
    {
        "id": "confront_leader",
        "title": "面對哥布林首領，決定人質的命運",
        "nudge": "足跡通往山坡裂隙——深入巢穴追查首領葛利克斯。",
        "done_flags": ["climax_resolved"],
        "done_if_reached": [],
        "terminal": True,
    },
]


ENDINGS: dict[str, str] = {
    "victory": (
        "哥布林被擊敗，人質重獲自由，隊伍帶著倖存者回到晨橋村。老佩林如釋重負地落淚，"
        "付出承諾的金幣，還多添了一點謝禮。接下來數週，鎏金酒杯酒館都會傳唱晨橋救援的故事。"
        "**劇終。**"
    ),
    "peaceful": (
        "沒有任何致命一擊，隊伍帶著獲救的人質走出巢穴。新冒險者既勇敢又聰明的名聲傳開。"
        "老佩林全額支付報酬，就連哥布林也記住了那天：他們不是被屠殺，而是被智取。**劇終。**"
    ),
    "defeat": (
        "巢穴裡只剩哥布林的笑聲迴盪，隊伍的故事在黑暗中告終。但在活生生的世界裡，"
        "即使倒下的人也會被記得。**劇終。**"
    ),
}


def scene_by_id(scene_id: str) -> dict | None:
    return next((s for s in SCENES if s["id"] == scene_id), None)


def scene_index(scene_id: str) -> int:
    for i, s in enumerate(SCENES):
        if s["id"] == scene_id:
            return i
    return -1


def first_scene() -> dict:
    return SCENES[0]


def next_scene(scene_id: str) -> dict | None:
    i = scene_index(scene_id)
    if i < 0 or i + 1 >= len(SCENES):
        return None
    return SCENES[i + 1]
