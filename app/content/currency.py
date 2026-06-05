"""Currency denominations and parser helpers for quantified coin grants."""
from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class CurrencyGrant:
    denomination: str
    item_id: str
    item_name: str
    quantity: int


DENOMINATIONS: dict[str, dict] = {
    "金幣": {
        "item_id": "item_gold_coin",
        "copper_value": 1000,
        "aliases": ["gold coin", "gold coins", "gp"],
    },
    "銀幣": {
        "item_id": "item_silver_coin",
        "copper_value": 100,
        "aliases": ["silver coin", "silver coins", "sp"],
    },
    "銅幣": {
        "item_id": "item_copper_coin",
        "copper_value": 1,
        "aliases": ["copper coin", "copper coins", "cp"],
    },
}

QUANTIFIERS: dict[str, int] = {
    "枚": 1,
    "少許": 10,
    "把": 10,
    "小袋": 50,
    "袋": 100,
    "大袋": 500,
    "箱": 2000,
}

ABSTRACT_ALIASES: dict[str, str] = {
    "一點": "少許",
    "一點點": "少許",
    "些許": "少許",
    "少量": "少許",
    "幾枚": "少許",
    "幾個": "少許",
    "一小撮": "把",
    "一撮": "把",
    "一小包": "小袋",
    "一小袋": "小袋",
    "一些": "袋",
    "一批": "袋",
    "一堆": "袋",
    "很多": "袋",
    "大量": "袋",
    "滿袋": "袋",
    "滿滿一袋": "袋",
    "一大包": "大袋",
    "一大袋": "大袋",
    "一箱": "箱",
    "整箱": "箱",
    "滿箱": "箱",
}

_DIGITS_ZH = {
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

_MONEY_WORDS = ("錢", "錢幣", "貨幣", "金幣", "銀幣", "銅幣")
_DENOM_RE = re.compile("|".join(re.escape(k) for k in DENOMINATIONS))
_ARABIC_AMOUNT_RE = re.compile(r"(?P<count>\d+)\s*(?:枚|個)?\s*(?P<denom>金幣|銀幣|銅幣)")


def coin_item_defs() -> list[dict]:
    """Canonical seed item definitions for the three coin denominations."""
    return [
        {
            "id": data["item_id"],
            "name": name,
            "aliases": list(data["aliases"]),
            "category": "treasure",
            "slot": None,
            "description": f"標準貨幣：1 {name} = {data['copper_value']} 銅幣。",
            "stackable": True,
            "source": "seed",
        }
        for name, data in DENOMINATIONS.items()
    ]


def normalize_currency_item(item_name: str) -> tuple[str, str] | None:
    """Return (item_id, canonical_name) when the text names a known coin."""
    denom = _extract_denomination(item_name)
    if not denom:
        return None
    return DENOMINATIONS[denom]["item_id"], denom


def currency_value_in_copper(denomination: str, quantity: int) -> int:
    if denomination not in DENOMINATIONS:
        raise ValueError(f"unknown currency denomination: {denomination}")
    return max(0, int(quantity)) * int(DENOMINATIONS[denomination]["copper_value"])


def currency_quantifier_examples() -> dict[str, int]:
    return {
        "一枚金幣": 1,
        "少許銀幣": 10,
        "幾枚銀幣": 10,
        "一點銅幣": 10,
        "一把金幣": 10,
        "一小袋銀幣": 50,
        "一些銀幣": 100,
        "很多金幣": 100,
        "一大袋銅幣": 500,
        "一箱銅幣": 2000,
        "兩把金幣": 20,
        "三小袋銀幣": 150,
        "2箱銅幣": 4000,
    }


def looks_like_currency(text: str | None) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    if any(word in low for word in _MONEY_WORDS):
        return True
    return any(alias in low for data in DENOMINATIONS.values() for alias in data["aliases"])


def parse_currency_grant(item_name: str, quantity: int = 1) -> CurrencyGrant | None:
    """Parse a currency grant into a canonical denomination and exact coin quantity.

    Returns None both for non-currency text and invalid currency text. Call
    looks_like_currency() first when the caller needs to distinguish those cases.
    """
    text = _compact(item_name)
    if not text:
        return None

    denom = _extract_denomination(text)
    if not denom:
        return None

    embedded_qty = _parse_embedded_quantity(text, denom)
    if embedded_qty is None:
        # A bare canonical coin name uses the structured ItemGrant.quantity.
        if text == denom or _is_alias_for(text, denom):
            embedded_qty = 1
        else:
            return None

    total = max(1, int(quantity or 1)) * embedded_qty
    return CurrencyGrant(
        denomination=denom,
        item_id=DENOMINATIONS[denom]["item_id"],
        item_name=denom,
        quantity=total,
    )


def _compact(text: str | None) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def _extract_denomination(text: str | None) -> str | None:
    clean = _compact(text)
    if not clean:
        return None
    match = _DENOM_RE.search(clean)
    if match:
        return match.group(0)
    low = clean.lower()
    for denom, data in DENOMINATIONS.items():
        if low in {str(a).lower().replace(" ", "") for a in data["aliases"]}:
            return denom
    return None


def _parse_embedded_quantity(text: str, denom: str) -> int | None:
    match = _ARABIC_AMOUNT_RE.fullmatch(text)
    if match and match.group("denom") == denom:
        return int(match.group("count"))

    prefix = text.removesuffix(denom)
    if not prefix:
        return None

    alias_qty = _abstract_quantity(prefix)
    if alias_qty is not None:
        return alias_qty

    multiplier, unit = _split_multiplier_unit(prefix)
    if unit in QUANTIFIERS:
        return multiplier * QUANTIFIERS[unit]
    return None


def _abstract_quantity(prefix: str) -> int | None:
    unit = ABSTRACT_ALIASES.get(prefix)
    if unit is None:
        return None
    return QUANTIFIERS[unit]


def _split_multiplier_unit(prefix: str) -> tuple[int, str]:
    match = re.fullmatch(r"(?P<num>\d+)(?P<unit>枚|把|小袋|袋|大袋|箱)", prefix)
    if match:
        return int(match.group("num")), match.group("unit")

    for zh, value in sorted(_DIGITS_ZH.items(), key=lambda kv: len(kv[0]), reverse=True):
        if prefix.startswith(zh):
            return value, prefix[len(zh):]
    return 1, prefix


def _is_alias_for(text: str, denom: str) -> bool:
    low = text.lower()
    return low in {str(a).lower().replace(" ", "") for a in DENOMINATIONS[denom]["aliases"]}
