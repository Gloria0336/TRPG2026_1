"""Seed item catalog and free-text inventory helpers."""
from __future__ import annotations

import re


SEED_ITEMS: list[dict] = [
    {
        "id": "item_longsword",
        "name": "長劍",
        "aliases": ["一把長劍", "longsword"],
        "category": "weapon",
        "slot": "main_hand",
        "description": "標準單手長劍。",
        "stackable": False,
        "source": "seed",
    },
    {
        "id": "item_dagger",
        "name": "匕首",
        "aliases": ["一把匕首", "dagger"],
        "category": "weapon",
        "slot": "main_hand",
        "description": "短刃，可近戰或投擲。",
        "stackable": False,
        "source": "seed",
    },
    {
        "id": "item_chain_mail",
        "name": "鏈甲",
        "aliases": ["鎖子甲", "chain mail", "鏈甲與盾牌"],
        "category": "armor",
        "slot": "armor",
        "description": "金屬環編成的中型護甲。",
        "stackable": False,
        "source": "seed",
    },
    {
        "id": "item_shield",
        "name": "盾牌",
        "aliases": ["shield", "鏈甲與盾牌"],
        "category": "shield",
        "slot": "off_hand",
        "description": "木質或金屬包邊盾牌。",
        "stackable": False,
        "source": "seed",
    },
    {
        "id": "item_mace",
        "name": "釘頭錘",
        "aliases": ["mace"],
        "category": "weapon",
        "slot": "main_hand",
        "description": "沉重鈍器，適合敲碎護甲縫隙。",
        "stackable": False,
        "source": "seed",
    },
    {
        "id": "item_crossbow",
        "name": "重弩與弩矢",
        "aliases": ["重弩", "弩矢", "heavy crossbow", "crossbow bolts"],
        "category": "weapon",
        "slot": "main_hand",
        "description": "重弩和一束可用弩矢。",
        "stackable": False,
        "source": "seed",
    },
    {
        "id": "item_torch",
        "name": "火把",
        "aliases": ["火把數支", "torch", "torches"],
        "category": "gear",
        "slot": None,
        "description": "可照亮黑暗道路的乾燥火把。",
        "metadata": {"projection_name": "火把數支"},
        "stackable": True,
        "source": "seed",
    },
    {
        "id": "item_rope",
        "name": "繩索",
        "aliases": ["繩索 15 公尺", "rope"],
        "category": "gear",
        "slot": None,
        "description": "一捆約十五公尺的結實繩索。",
        "metadata": {"projection_name": "繩索 15 公尺"},
        "stackable": True,
        "source": "seed",
    },
    {
        "id": "item_rations_waterskin",
        "name": "乾糧與水袋",
        "aliases": ["乾糧水袋", "乾糧", "水袋", "rations", "waterskin"],
        "category": "gear",
        "slot": None,
        "description": "短途旅行用的食物與飲水容器。",
        "stackable": True,
        "source": "seed",
    },
    {
        "id": "item_healing_herbs",
        "name": "治療藥草",
        "aliases": ["治療藥水", "healing herbs", "healing potion"],
        "category": "consumable",
        "slot": None,
        "description": "能處理小傷口的草藥包。",
        "stackable": True,
        "source": "seed",
    },
    {
        "id": "item_holy_symbol",
        "name": "聖徽",
        "aliases": ["holy symbol"],
        "category": "key_item",
        "slot": "trinket",
        "description": "信仰儀式與祈禱時使用的聖徽。",
        "stackable": False,
        "source": "seed",
    },
    {
        "id": "item_prayer_book",
        "name": "祈禱書",
        "aliases": ["prayer book"],
        "category": "key_item",
        "slot": None,
        "description": "寫滿祈禱文與註記的小書。",
        "stackable": False,
        "source": "seed",
    },
    {
        "id": "item_silver_coins",
        "name": "少許銀幣",
        "aliases": ["銀幣", "silver coins"],
        "category": "treasure",
        "slot": None,
        "description": "數量不多、足以應急的銀幣。",
        "stackable": True,
        "source": "seed",
    },
]


_LEADING_FILLERS = ("一把", "一件", "一瓶", "一捆", "一些", "少量", "數支")
_QTY_RE = re.compile(r"(?:[xX×*]\s*\d+|\d+\s*(?:個|把|件|瓶|支|枚|公尺|米)?)$")


def normalize_name(name: str) -> str:
    """Canonicalize free-text item names for catalog dedupe."""
    text = (name or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    text = _QTY_RE.sub("", text).strip()
    for prefix in _LEADING_FILLERS:
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text


def parse_freetext(line: str) -> dict | None:
    """Best-effort mapping from legacy Character.inventory strings to item defs."""
    raw = (line or "").strip()
    if not raw:
        return None
    norm = normalize_name(raw)
    for item in SEED_ITEMS:
        names = [item["name"], *(item.get("aliases") or [])]
        if any(norm == normalize_name(name) for name in names):
            return dict(item)
    return {
        "name": raw,
        "aliases": [],
        "category": "misc",
        "slot": None,
        "description": "",
        "stackable": True,
        "source": "dynamic",
    }
