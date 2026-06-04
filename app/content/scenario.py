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
#
# This is a HIERARCHICAL WORLD GRAPH (design §6 location hierarchy):
#   • `loc_type` — region › settlement › venue / wilds / interior. Gives the graph levels.
#   • `parent`   — containment (a venue sits inside a settlement, a settlement inside a region).
#                  Vertical parent↔child edges are implicit and bidirectional (enter / exit).
#   • `connects` — lateral edges, normally between siblings sharing a parent. Declare them
#                  symmetrically (if A connects B, B connects A) so travel routes both ways.
#   • `travel_cost` — day-stages spent ENTERING this node (default 1; 0 for in-town hops so
#                  stepping out of the tavern is instant, while heading into the wilds burns time).
#   • `danger`   — 0 safe … 5 lethal. ≥3 soft-gates entry (warn, proceed; design §12 default).
#   • `required_rank` / `gate="hard"` — hard-gate entry (block). Reserved for future danger
#                  zones; the MVP scenario uses none so the climax stays reachable.
# Travel pathfinds over this graph (store.travel_path): non-adjacent targets are reached by
# routing THROUGH intermediate nodes (no teleport), accumulating travel_cost. Emergent places
# the players invent carry no adjacency and stay freely reachable.
# Each entry also carries an authored `card` — the stable sensory/description skin the
# narrator reads. Pre-authoring it (instead of generating it from the LLM at /start) keeps
# new-game latency flat: ensure_seed_location_cards persists these directly, skipping the
# per-location AI round-trip. Emergent player-invented places still get an AI-built card.
# Card fields mirror ai.schemas.LocationCard; `terrain_modifier` here is authoritative and
# must match the structural value above so the card upsert doesn't drift the travel cost.
LOCATIONS: list[dict] = [
    {"id": "frontier", "name": "晨橋邊境", "aliases": ["邊境", "這一帶", "周邊地帶"],
     "loc_type": "region", "travel_cost": 0,
     "distances": {"morningbridge": 1, "east_road": 5, "warren": 15},
     "notes": "晨橋村及其周邊的邊境地帶——村莊、東路與哥布林盤踞的山坡都屬此區。",
     "card": {
         "base_summary": "晨橋邊境是一片靜默的邊陲谷地：南邊炊煙裊裊的晨橋村，東路沿河谷沒入林線，"
                         "北側山坡的裂隙裡盤踞著哥布林。",
         "sensory_anchors": ["河水奔流的低響", "濕冷貼地的晨霧", "林木與濕泥的氣味"],
         "visual_landmarks": ["橫跨湍流的晨橋", "向東沒入森林的大路", "北面裸露的岩石山坡"],
         "interactive_features": ["路邊的里程石", "可遠眺四方的高地"],
         "discoverables": ["各村落與道路之間的相對方位", "近日往來商旅的蹤跡"],
         "hazards": ["離開村莊後逐漸逼近的哥布林威脅"],
         "soft_hooks": ["可前往晨橋村補給、沿東路追查，或遠望北方山坡"],
         "exits_hint": ["晨橋村", "東路", "哥布林巢穴"],
         "mood": "遼闊、靜謐、暗藏威脅",
         "terrain_modifier": 1.0,
     }},
    {"id": "morningbridge", "name": "晨橋村", "aliases": ["鎮上", "村子", "村莊", "晨橋"],
     "loc_type": "settlement", "parent": "frontier", "travel_cost": 0,
     "distances": {"frontier": 1, "tavern": 0.2, "east_road": 5},
     "notes": "寧靜的邊境村莊；鎏金酒杯酒館與往東的大路都在這裡。",
     "connects": ["east_road"],
     "card": {
         "base_summary": "晨橋村是一座寧靜的邊境小村，木屋沿河岸聚攏；鎏金酒杯酒館的燈火與往東的大路都從這裡開始。",
         "sensory_anchors": ["河水拍打橋墩的聲音", "炊煙與烤麵包香", "泥土小徑的踩踏聲"],
         "visual_landmarks": ["鎏金酒杯酒館的招牌", "橫跨河川的木橋", "村口往東的路標"],
         "interactive_features": ["村中的告示板", "井邊閒談的村民"],
         "discoverables": ["商隊失蹤的鄉里傳聞", "民兵不願出動搜救的緣由"],
         "hazards": [],
         "soft_hooks": ["可進酒館打聽、向村民攀談，或沿東路出發"],
         "exits_hint": ["鎏金酒杯酒館", "東路"],
         "mood": "寧靜、溫暖、略帶不安",
         "terrain_modifier": 1.0,
     }},
    {"id": "tavern", "name": "鎏金酒杯酒館", "aliases": ["酒館", "鎏金酒杯", "鎏金酒杯酒館"],
     "loc_type": "venue", "parent": "morningbridge", "travel_cost": 0,
     "distances": {"morningbridge": 0.2},
     "notes": "晨橋村熱鬧的酒館，冒險的起點。",
     "card": {
         "base_summary": "鎏金酒杯酒館燈火通明、人聲鼎沸，是冒險的起點；焦急的商人老佩林在此求助，"
                         "角落還坐著一名偷瞄他的兜帽客。",
         "sensory_anchors": ["爐火劈啪作響", "麥酒與烤肉的香氣", "壓低嗓音的交談與杯盤碰撞"],
         "visual_landmarks": ["燃著爐火的壁爐", "酒保擦拭酒杯的吧台", "光線昏暗的角落座位"],
         "interactive_features": ["可請人喝酒攀談的吧台", "可旁聽的鄰桌", "張貼委託的牆面"],
         "discoverables": ["商隊原訂的去向與路線", "角落兜帽客的可疑舉動", "民兵推託搜救的內情"],
         "hazards": [],
         "soft_hooks": ["可與老佩林深談、留意兜帽客，或接下委託動身"],
         "exits_hint": ["晨橋村"],
         "mood": "熱鬧、溫暖、暗流湧動",
         "terrain_modifier": 1.0,
     }},
    {"id": "east_road", "name": "東路", "aliases": ["東邊道路", "大路", "東面道路"],
     "loc_type": "wilds", "parent": "frontier", "travel_cost": 1,
     "distances": {"frontier": 5, "morningbridge": 5, "warren": 10},
     "terrain_modifier": 0.9,
     "notes": "晨橋村往東的道路，商隊失蹤之處。",
     "connects": ["morningbridge", "warren"],
     "card": {
         "base_summary": "東路在晨霧中逐漸收窄沒入森林，路上留著兩輛翻覆的貨車、散落的箱子與深色血跡；"
                         "足跡離開道路通往北面山坡，前方小徑還有一條反光的絆線。",
         "sensory_anchors": ["貼地不散的晨霧", "潮濕泥土與鐵鏽血腥味", "林間鳥獸的零星聲響"],
         "visual_landmarks": ["兩輛翻覆的貨車", "離開路面的粗糙足跡", "小徑上微微反光的絆線"],
         "interactive_features": ["可翻找的貨車與散箱", "可追蹤的足跡", "可拆解或迴避的陷阱"],
         "discoverables": ["襲擊者是哥布林的線索", "幾乎不見屍體的蹊蹺", "通往巢穴的路徑"],
         "hazards": ["小徑上隱蔽的絆線陷阱", "脫離道路後逼近的伏擊"],
         "soft_hooks": ["可搜索貨車、循足跡追查，或先解除絆線"],
         "exits_hint": ["晨橋村", "哥布林巢穴"],
         "mood": "陰冷、警戒、不祥",
         "terrain_modifier": 0.9,
     }},
    {"id": "warren", "name": "哥布林巢穴", "aliases": ["巢穴", "哥布林窩", "山坡裂隙"],
     "loc_type": "wilds", "parent": "frontier", "travel_cost": 1, "danger": 3,
     "distances": {"frontier": 15, "east_road": 10},
     "terrain_modifier": 0.7,
     "notes": "山坡裂隙後、煙霧瀰漫的哥布林巢穴。",
     "connects": ["east_road"],
     "card": {
         "base_summary": "哥布林巢穴藏在山坡裂隙之後，煙霧瀰漫、惡臭撲鼻；深處哥布林首領葛利克斯正用刀"
                         "挾持一名商隊護衛，身旁還有一名副手。",
         "sensory_anchors": ["嗆人的木煙與哥布林臭味", "深處傳來的低吼與騷動", "滴水聲與晃動的火光"],
         "visual_landmarks": ["冒著煙的山坡裂隙入口", "狹窄曲折的洞道", "首領盤踞的內穴"],
         "interactive_features": ["可潛行繞行的陰影", "可談判或威嚇的對象", "可掩護移動的岩柱"],
         "discoverables": ["人質的位置與狀態", "巢穴的退路", "首領的弱點與動機"],
         "hazards": ["挾持人質的哥布林首領與副手", "狹道中的伏擊與火煙"],
         "soft_hooks": ["可正面開戰、出言談判，或潛行救出人質"],
         "exits_hint": ["東路"],
         "mood": "壓迫、危險、緊繃",
         "terrain_modifier": 0.7,
     }},
]

# Per-scene cost pools (design §4.7). When a check lands in FAILURE or CRIT_FAILURE the
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
                "flags": {"movement_base": 2},
            },
            {
                "id": "ent_hooded", "kind": "person", "name": "緊張的兜帽客",
                "aliases": ["兜帽客", "兜帽人", "神秘人", "兜帽客人"],
                "status": "present", "disposition": "afraid",
                "notes": "坐在角落，不時偷瞄佩林。",
                "flags": {"agenda": "暗中監視佩林，怕商隊真相被查出；情勢不對就設法溜走", "movement_base": 5},
            },
        ],
        "challenges": {
            "diplomacy": 11,
            "perception": 13,
            "intimidation": 12,
        },
        "cost_pool": ["relation", "attention", "time"],
        "onboarding": [
            "請老佩林喝一杯，問清楚商隊原本要去哪裡。（交涉/察覺）",
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
            "perception": 15,
            "survival": 15,
            "acrobatics": 15,
        },
        "cost_pool": ["time", "trace", "resource"],
        "onboarding": [
            "搜索翻覆貨車，尋找線索。（察覺）",
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
            "diplomacy": 15,
            "intimidation": 15,
            "stealth": 15,
            "deception": 15,
        },
        "cost_pool": ["exposure", "attention", "relation"],
        "onboarding": [
            "戰鬥：衝向葛利克斯！（開始與首領和副手交戰）",
            "談判：試著說服葛利克斯拿走金幣，放走護衛。（交涉）",
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
