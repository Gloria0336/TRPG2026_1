"""Guild rank, merit, promotion, and rank-gate helpers."""
from __future__ import annotations

from .types import Character, Cost, CostSeverity, CostType


RANK_ORDER: tuple[str, ...] = ("F", "E", "D", "C", "B", "A", "S")
RANK_LEVEL_BANDS: dict[str, tuple[int, int]] = {
    "F": (1, 2),
    "E": (3, 4),
    "D": (5, 7),
    "C": (8, 10),
    "B": (11, 13),
    "A": (14, 17),
    "S": (18, 20),
}
RANK_THRESHOLDS: dict[str, int] = {
    "E": 10,
    "D": 25,
    "C": 60,
    "B": 140,
    "A": 320,
    "S": 800,
}

UNDER_RANK_DC_STEP = 2


class GuildRankError(ValueError):
    """Raised when a guild-rank operation is not legal."""


def normalize_rank(rank: str | None) -> str:
    value = str(rank or "F").strip().upper()
    return value if value in RANK_ORDER else "F"


def rank_index(rank: str | None) -> int:
    return RANK_ORDER.index(normalize_rank(rank))


def next_guild_rank(rank: str | None) -> str | None:
    idx = rank_index(rank)
    if idx >= len(RANK_ORDER) - 1:
        return None
    return RANK_ORDER[idx + 1]


def award_merit(char: Character, amount: int) -> int:
    gained = max(0, int(amount))
    char.merit += gained
    check_promotion_available(char)
    return char.merit


def check_promotion_available(char: Character) -> bool:
    nxt = next_guild_rank(char.guild_rank)
    if nxt is None:
        char.rank_flags.pop("promotion_available", None)
        return False
    available = char.merit >= RANK_THRESHOLDS[nxt]
    if available:
        char.rank_flags["promotion_available"] = nxt
    else:
        char.rank_flags.pop("promotion_available", None)
    return available


def promote(char: Character, *, require_available: bool = True) -> str:
    nxt = next_guild_rank(char.guild_rank)
    if nxt is None:
        raise GuildRankError("already at top guild rank")
    if require_available and char.rank_flags.get("promotion_available") != nxt:
        raise GuildRankError(f"promotion to {nxt} is not available")
    char.guild_rank = nxt
    char.merit = max(0, char.merit - RANK_THRESHOLDS[nxt])
    char.rank_flags.pop("promotion_available", None)
    char.rank_flags.pop("suspended", None)
    check_promotion_available(char)
    return nxt


def demote(char: Character) -> str:
    idx = rank_index(char.guild_rank)
    if idx <= 0:
        raise GuildRankError("already at lowest guild rank")
    char.guild_rank = RANK_ORDER[idx - 1]
    char.rank_flags["demotion_discount"] = True
    char.rank_flags.pop("promotion_available", None)
    return char.guild_rank


def rank_gate(min_rank: str | None, actor: Character) -> bool:
    return rank_index(actor.guild_rank) >= rank_index(min_rank)


def under_rank_dc_penalty(min_rank: str | None, actor: Character) -> int:
    gap = rank_index(min_rank) - rank_index(actor.guild_rank)
    return max(0, gap) * UNDER_RANK_DC_STEP


def institutional_consequence(
    char: Character,
    *,
    severe: bool = False,
    note: str = "",
) -> Cost:
    """Apply non-level, non-skill consequences for death or disastrous failure."""
    char.standing -= 2 if severe else 1
    char.rank_flags["suspended"] = True
    char.rank_flags["reinstatement_quest"] = True
    severity = CostSeverity.HEAVY if severe else CostSeverity.MODERATE
    return Cost(
        type=CostType.DEBT,
        severity=severity,
        persistent=True,
        note=note or "公會資格暫停與復權債務",
    )
