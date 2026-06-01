from app.engine import dice


def test_seed_determinism():
    dice.reseed(42)
    a = [dice.roll_d20(3).total for _ in range(10)]
    dice.reseed(42)
    b = [dice.roll_d20(3).total for _ in range(10)]
    assert a == b


def test_d20_range_and_total():
    for _ in range(200):
        r = dice.roll_d20(5)
        assert 1 <= r.natural <= 20
        assert r.total == r.natural + 5


def test_advantage_takes_higher():
    dice.reseed(1)
    for _ in range(100):
        r = dice.roll_d20(0, advantage=True)
        assert r.natural == max(r.rolls)
        assert len(r.rolls) == 2


def test_disadvantage_takes_lower():
    dice.reseed(2)
    for _ in range(100):
        r = dice.roll_d20(0, disadvantage=True)
        assert r.natural == min(r.rolls)


def test_advantage_and_disadvantage_cancel():
    r = dice.roll_d20(0, advantage=True, disadvantage=True)
    assert len(r.rolls) == 1
    assert not r.advantage and not r.disadvantage


def test_crit_doubles_dice():
    dice.reseed(7)
    normal = dice.roll_dice(2, 6, 3)
    dice.reseed(7)
    crit = dice.roll_dice(2, 6, 3, crit=True)
    assert len(normal.rolls) == 2
    assert len(crit.rolls) == 4  # dice doubled, bonus not


def test_parse_and_roll():
    r = dice.parse_and_roll("2d6+3")
    assert len(r.rolls) == 2
    assert r.bonus == 3
    assert r.total == sum(r.rolls) + 3
    assert dice.parse_and_roll("d20").bonus == 0


def test_parse_rejects_garbage():
    import pytest
    with pytest.raises(ValueError):
        dice.parse_and_roll("hello")
