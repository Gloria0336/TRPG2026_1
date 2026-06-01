"""Dice — the ONLY place in the codebase where randomness happens.

Design §4.0 / §9.3: the server computes the real roll; the front-end animation
merely "plays" toward an already-decided number. Keeping every roll behind this
module makes the engine auditable and (with a seed) deterministic for tests.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from ..config import settings

# Module-level RNG. Seedable for deterministic tests via reseed().
_rng = random.Random(settings.dice_seed)


def reseed(seed: int | None) -> None:
    """Reset the shared RNG. Pass an int for deterministic rolls, None for entropy."""
    global _rng
    _rng = random.Random(seed)


@dataclass(frozen=True)
class D20Roll:
    """Result of a single d20 check, with full breakdown for the reveal embed."""

    rolls: tuple[int, ...]          # the raw die/dice rolled (1 or 2 for adv/dis)
    natural: int                    # the d20 face actually used
    modifier: int                   # total flat modifier added
    total: int                      # natural + modifier
    advantage: bool = False
    disadvantage: bool = False

    @property
    def is_crit(self) -> bool:
        """Natural 20 (used face)."""
        return self.natural == 20

    @property
    def is_fumble(self) -> bool:
        """Natural 1 (used face)."""
        return self.natural == 1

    def breakdown(self) -> str:
        """Human-readable breakdown, e.g. 'd20(14) + 5 = 19' or adv '[14, 8]→14 + 5 = 19'."""
        sign = "+" if self.modifier >= 0 else "-"
        mod = abs(self.modifier)
        if self.advantage or self.disadvantage:
            tag = "adv" if self.advantage else "dis"
            return f"d20[{', '.join(map(str, self.rolls))}]→{self.natural} ({tag}) {sign} {mod} = {self.total}"
        return f"d20({self.natural}) {sign} {mod} = {self.total}"


def roll_d20(modifier: int = 0, *, advantage: bool = False, disadvantage: bool = False) -> D20Roll:
    """Roll a d20 + modifier. Advantage/disadvantage cancel out (5e rule)."""
    if advantage and disadvantage:
        advantage = disadvantage = False

    if advantage or disadvantage:
        a, b = _rng.randint(1, 20), _rng.randint(1, 20)
        natural = max(a, b) if advantage else min(a, b)
        rolls = (a, b)
    else:
        natural = _rng.randint(1, 20)
        rolls = (natural,)

    return D20Roll(
        rolls=rolls,
        natural=natural,
        modifier=modifier,
        total=natural + modifier,
        advantage=advantage,
        disadvantage=disadvantage,
    )


@dataclass(frozen=True)
class DiceRoll:
    """Result of rolling NdM (+ bonus), e.g. damage 2d6+3."""

    notation: str
    rolls: tuple[int, ...]
    bonus: int
    total: int

    def breakdown(self) -> str:
        inner = " + ".join(map(str, self.rolls))
        if self.bonus:
            sign = "+" if self.bonus >= 0 else "-"
            return f"{self.notation}: ({inner}) {sign} {abs(self.bonus)} = {self.total}"
        return f"{self.notation}: ({inner}) = {self.total}"


def roll_dice(count: int, sides: int, bonus: int = 0, *, crit: bool = False) -> DiceRoll:
    """Roll `count`d`sides` + bonus. On a crit, dice (not the bonus) are doubled (5e)."""
    n = count * 2 if crit else count
    rolls = tuple(_rng.randint(1, sides) for _ in range(n))
    total = sum(rolls) + bonus
    notation = f"{n}d{sides}" + (f"+{bonus}" if bonus > 0 else f"{bonus}" if bonus < 0 else "")
    return DiceRoll(notation=notation, rolls=rolls, bonus=bonus, total=max(0, total))


def parse_and_roll(notation: str) -> DiceRoll:
    """Roll a simple 'NdM+K' / 'NdM-K' / 'dM' / 'NdM' string. Used by /roll."""
    import re

    m = re.fullmatch(r"\s*(\d*)\s*d\s*(\d+)\s*([+-]\s*\d+)?\s*", notation, re.IGNORECASE)
    if not m:
        raise ValueError(f"Unrecognised dice notation: {notation!r}")
    count = int(m.group(1)) if m.group(1) else 1
    sides = int(m.group(2))
    bonus = int(m.group(3).replace(" ", "")) if m.group(3) else 0
    if count < 1 or count > 100 or sides < 2 or sides > 1000:
        raise ValueError(f"Dice out of range: {notation!r}")
    return roll_dice(count, sides, bonus)
