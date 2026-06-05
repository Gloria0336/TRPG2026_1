import pytest

from app.content.characters import premade_pcs
from app.engine import progression
from app.engine.types import Character


def test_bounded_rank_bonus_preserves_existing_trained_math():
    bram, _ = premade_pcs()
    assert bram.skill_bonus("athletics") == 5

    bram.skill_prof["athletics"] = "expert"
    assert bram.skill_bonus("athletics") == 7

    bram.level = 20
    bram.skill_prof["athletics"] = "legendary"
    assert bram.skill_bonus("athletics") == 15  # STR +3, bounded rank +12


def test_legacy_prof_values_load_as_new_ranks():
    c = Character.from_dict({
        "id": "pc_old",
        "name": "Old Save",
        "is_pc": True,
        "level": 3,
        "abilities": {"STR": 16},
        "max_hp": 10,
        "hp": 10,
        "ac": 12,
        "skill_prof": {"athletics": "prof", "stealth": "expertise"},
        "actions": [],
    })

    assert c.skill_prof["athletics"] == "trained"
    assert c.skill_prof["stealth"] == "master"
    assert c.skill_points == 0
    assert c.guild_rank == "F"


def test_skill_points_train_and_increase_skill_with_level_cap():
    bram, _ = premade_pcs()
    progression.grant_skill_points(bram, 10)

    assert progression.train_new_skill(bram, "stealth") == "trained"
    assert bram.skill_points == 9
    assert progression.increase_skill(bram, "stealth") == "expert"
    assert bram.skill_points == 7

    with pytest.raises(progression.ProgressionError):
        progression.increase_skill(bram, "stealth")  # master needs level 7


def test_lore_uses_int_and_guild_rank_does_not_affect_skill_bonus():
    bram, _ = premade_pcs()
    progression.grant_skill_points(bram, 2)
    progression.add_lore(bram, "龍_lore")
    assert bram.lore_bonus("龍_lore") == 2  # INT +0, trained pb +2

    before = bram.skill_bonus("athletics")
    bram.guild_rank = "S"
    assert bram.skill_bonus("athletics") == before
