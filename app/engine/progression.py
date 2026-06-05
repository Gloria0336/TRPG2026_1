"""Skill growth helpers for the bounded PF2e-shaped proficiency model."""
from __future__ import annotations

from .types import Character, PROFICIENCY_ORDER, ProficiencyRank, SKILLS, normalize_proficiency_rank


RANK_COST: dict[str, int] = {
    ProficiencyRank.TRAINED.value: 1,
    ProficiencyRank.EXPERT.value: 2,
    ProficiencyRank.MASTER.value: 3,
    ProficiencyRank.LEGENDARY.value: 4,
}

RANK_LEVEL_MIN: dict[str, int] = {
    ProficiencyRank.TRAINED.value: 1,
    ProficiencyRank.EXPERT.value: 3,
    ProficiencyRank.MASTER.value: 7,
    ProficiencyRank.LEGENDARY.value: 15,
}


class ProgressionError(ValueError):
    """Raised when a requested skill progression is not currently legal."""


def grant_skill_points(char: Character, amount: int) -> int:
    gained = max(0, int(amount))
    char.skill_points += gained
    return char.skill_points


def _rank_index(rank: str) -> int:
    return PROFICIENCY_ORDER.index(normalize_proficiency_rank(rank))


def next_rank(current: str | None) -> str | None:
    idx = _rank_index(normalize_proficiency_rank(current))
    if idx >= len(PROFICIENCY_ORDER) - 1:
        return None
    return PROFICIENCY_ORDER[idx + 1]


def can_reach_rank(char: Character, rank: str) -> bool:
    return char.level >= RANK_LEVEL_MIN.get(normalize_proficiency_rank(rank), 1)


def _spend_for_rank(char: Character, rank: str) -> None:
    target = normalize_proficiency_rank(rank)
    if target == ProficiencyRank.UNTRAINED.value:
        return
    if not can_reach_rank(char, target):
        raise ProgressionError(f"{target} requires level {RANK_LEVEL_MIN[target]}")
    cost = RANK_COST[target]
    if char.skill_points < cost:
        raise ProgressionError(f"not enough skill points: need {cost}")
    char.skill_points -= cost


def increase_skill(char: Character, skill: str) -> str:
    """Raise an existing standard skill by one rank and return the new rank."""
    key = skill.strip().lower()
    if key not in SKILLS:
        raise ProgressionError(f"unknown skill: {skill}")
    current = normalize_proficiency_rank(char.skill_prof.get(key))
    target = next_rank(current)
    if target is None:
        raise ProgressionError(f"{key} is already legendary")
    _spend_for_rank(char, target)
    char.skill_prof[key] = target
    return target


def train_new_skill(char: Character, skill: str) -> str:
    """Train a currently untrained standard skill."""
    key = skill.strip().lower()
    if key not in SKILLS:
        raise ProgressionError(f"unknown skill: {skill}")
    if normalize_proficiency_rank(char.skill_prof.get(key)) != ProficiencyRank.UNTRAINED.value:
        raise ProgressionError(f"{key} is already trained")
    _spend_for_rank(char, ProficiencyRank.TRAINED.value)
    char.skill_prof[key] = ProficiencyRank.TRAINED.value
    return ProficiencyRank.TRAINED.value


def add_lore(char: Character, lore: str) -> str:
    """Add a named INT-based Lore at trained rank."""
    key = lore.strip()
    if not key:
        raise ProgressionError("lore name is required")
    if normalize_proficiency_rank(char.lore_prof.get(key)) != ProficiencyRank.UNTRAINED.value:
        raise ProgressionError(f"{key} lore already exists")
    _spend_for_rank(char, ProficiencyRank.TRAINED.value)
    char.lore_prof[key] = ProficiencyRank.TRAINED.value
    return ProficiencyRank.TRAINED.value


def increase_lore(char: Character, lore: str) -> str:
    key = lore.strip()
    if key not in char.lore_prof:
        raise ProgressionError(f"unknown lore: {lore}")
    current = normalize_proficiency_rank(char.lore_prof.get(key))
    target = next_rank(current)
    if target is None:
        raise ProgressionError(f"{key} lore is already legendary")
    _spend_for_rank(char, target)
    char.lore_prof[key] = target
    return target


def retrain_skill(char: Character, from_skill: str, to_skill: str) -> None:
    """Move one trained standard skill rank to another untrained standard skill."""
    src = from_skill.strip().lower()
    dst = to_skill.strip().lower()
    if src not in SKILLS or dst not in SKILLS:
        raise ProgressionError("retraining requires standard skills")
    src_rank = normalize_proficiency_rank(char.skill_prof.get(src))
    if src_rank == ProficiencyRank.UNTRAINED.value:
        raise ProgressionError(f"{src} is untrained")
    if normalize_proficiency_rank(char.skill_prof.get(dst)) != ProficiencyRank.UNTRAINED.value:
        raise ProgressionError(f"{dst} is already trained")
    char.skill_prof.pop(src, None)
    char.skill_prof[dst] = src_rank
