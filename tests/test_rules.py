from app.content.characters import premade_pcs
from app.content.monsters import spawn
from app.engine import rules_5e
from app.engine.types import ability_modifier, proficiency_bonus


def test_ability_modifier():
    assert ability_modifier(10) == 0
    assert ability_modifier(16) == 3
    assert ability_modifier(8) == -1
    assert ability_modifier(20) == 5


def test_proficiency_bonus():
    assert proficiency_bonus(1) == 2
    assert proficiency_bonus(4) == 2
    assert proficiency_bonus(5) == 3
    assert proficiency_bonus(20) == 6


def test_skill_and_save_bonuses():
    bram, lyra = premade_pcs()
    # Bram: Athletics proficient, STR 16(+3), prof +2 → +5
    assert bram.skill_bonus("athletics") == 5
    # Lyra: not proficient in athletics, STR 12(+1) → +1
    assert lyra.skill_bonus("athletics") == 1
    # Bram saves: STR proficient → +3 +2 = +5; DEX not → +1
    assert bram.save_bonus("STR") == 5
    assert bram.save_bonus("DEX") == 1


def test_nearest_anchor():
    assert rules_5e.nearest_anchor(13) == 15
    assert rules_5e.nearest_anchor(11) == 10
    # §4.3 six-tier ladder tops out at 30 (傳說/legendary) now.
    assert rules_5e.nearest_anchor(99) == 30


def test_dc_from_band_base_table():
    # Band selects the base DC (action-method difficulty); env modifier defaults to 0.
    for band, expected in rules_5e.BAND_DC.items():
        final, base, env = rules_5e.dc_from_band(band)
        assert (final, base, env) == (expected, expected, 0)


def test_dc_from_band_adds_env_modifier():
    # base 5 (very_easy) − 3 (favourable env) = DC 2; can drop below the ladder floor.
    assert rules_5e.dc_from_band("very_easy", -3) == (2, 5, -3)
    # base 25 (extreme) + 4 (hostile env) = DC 29.
    assert rules_5e.dc_from_band("extreme", 4) == (29, 25, 4)


def test_dc_from_band_clamps_env_and_floor():
    # env modifier is clamped to ±ENV_MODIFIER_CAP …
    final, base, env = rules_5e.dc_from_band("normal", 99)
    assert env == rules_5e.ENV_MODIFIER_CAP and final == 15 + rules_5e.ENV_MODIFIER_CAP
    # … and the final DC never falls below MIN_DC.
    final, base, env = rules_5e.dc_from_band("very_easy", -99)
    assert env == -rules_5e.ENV_MODIFIER_CAP and final == rules_5e.MIN_DC


def test_unknown_band_falls_back_to_normal():
    assert rules_5e.dc_from_band("bogus") == (15, 15, 0)


def test_apply_damage_downs_pc():
    bram, _ = premade_pcs()
    deltas = rules_5e.apply_damage(bram, bram.max_hp)
    assert bram.hp == 0
    assert bram.is_down
    assert "unconscious" in bram.conditions
    assert not bram.is_dead
    assert any("0 HP" in d or "unconscious" in d for d in deltas)


def test_massive_damage_instant_death():
    bram, _ = premade_pcs()
    rules_5e.apply_damage(bram, bram.max_hp * 2 + 1)
    assert bram.is_dead


def test_monster_dies_at_zero():
    goblin = spawn("goblin", 1)
    rules_5e.apply_damage(goblin, 999)
    assert goblin.is_dead
    assert goblin.hp == 0


def test_healing_revives_downed_pc():
    bram, _ = premade_pcs()
    rules_5e.apply_damage(bram, bram.max_hp)
    assert bram.is_down
    rules_5e.apply_healing(bram, 5)
    assert bram.hp == 5
    assert "unconscious" not in bram.conditions


def test_check_result_structure():
    bram, _ = premade_pcs()
    res = rules_5e.ability_check(bram, "athletics", 10)
    assert res.dc == 10
    assert res.natural is not None
    assert isinstance(res.success, bool)
    assert "Athletics" in res.summary


def test_attack_applies_damage_on_hit():
    bram, _ = premade_pcs()
    goblin = spawn("goblin", 1)
    sword = bram.find_action("Longsword")
    # Run several attacks; at least one should hit a low-AC goblin and reduce HP.
    start = goblin.hp
    for _ in range(20):
        if goblin.is_dead:
            break
        rules_5e.attack(bram, goblin, sword)
    assert goblin.hp < start


def test_heal_action_caps_at_max():
    _, lyra = premade_pcs()
    cure = lyra.find_action("Cure Wounds")
    lyra.hp = 1
    res = rules_5e.heal(lyra, lyra, cure)
    assert lyra.hp <= lyra.max_hp
    assert res.healing is not None and res.healing > 0
