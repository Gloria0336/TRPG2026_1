from app.content import currency
from app.db import store
from app.state import game_state


def _parsed(text: str, quantity: int = 1):
    grant = currency.parse_currency_grant(text, quantity)
    assert grant is not None
    return grant


def test_currency_parser_accepts_fixed_and_abstract_quantifiers():
    cases = {
        "一枚金幣": ("金幣", 1),
        "少許銀幣": ("銀幣", 10),
        "幾枚銀幣": ("銀幣", 10),
        "一點銅幣": ("銅幣", 10),
        "一把金幣": ("金幣", 10),
        "一小袋銀幣": ("銀幣", 50),
        "一些銀幣": ("銀幣", 100),
        "很多金幣": ("金幣", 100),
        "一大袋銅幣": ("銅幣", 500),
        "一箱銅幣": ("銅幣", 2000),
        "50 金幣": ("金幣", 50),
        "50枚金幣": ("金幣", 50),
    }
    for text, expected in cases.items():
        grant = _parsed(text)
        assert (grant.item_name, grant.quantity) == expected


def test_currency_parser_accepts_multiplier_quantifiers():
    cases = {
        "兩把金幣": ("金幣", 20),
        "三小袋銀幣": ("銀幣", 150),
        "2箱銅幣": ("銅幣", 4000),
        "兩大包銀幣": ("銀幣", 1000),
    }
    for text, expected in cases.items():
        grant = _parsed(text)
        assert (grant.item_name, grant.quantity) == expected


def test_currency_parser_rejects_undenominated_money():
    for text in ("很多錢", "一袋錢幣", "一大筆錢"):
        assert currency.looks_like_currency(text)
        assert currency.parse_currency_grant(text) is None


def test_store_grants_canonical_currency_and_merges_quantity():
    one = store.grant_item("pc_bram", "兩把金幣")
    two = store.grant_item("pc_bram", "金幣", quantity=5)
    store.grant_item("pc_bram", "一小袋銀幣")
    store.grant_item("pc_bram", "一箱銅幣")

    assert one["item_id"] == "item_gold_coin"
    assert two["item_id"] == "item_gold_coin"
    assert two["quantity"] == 25

    inventory = {item["name"]: item for item in store.get_inventory("pc_bram")}
    assert inventory["金幣"]["quantity"] == 25
    assert inventory["銀幣"]["quantity"] == 50
    assert inventory["銅幣"]["quantity"] == 2000


def test_store_rejects_unquantified_currency_grants():
    try:
        store.grant_item("pc_bram", "很多錢")
    except ValueError:
        pass
    else:
        raise AssertionError("unquantified currency grant should be rejected")
    assert store.get_inventory("pc_bram") == []


def test_new_game_migrates_legacy_minor_silver_to_quantified_coins():
    gs = game_state.new_game(channel_id=1)

    bram_silver = store.get_inventory_item("pc_bram", "銀幣")
    lyra_silver = store.get_inventory_item("pc_lyra", "銀幣")

    assert bram_silver is not None
    assert lyra_silver is not None
    assert bram_silver["quantity"] == 10
    assert lyra_silver["quantity"] == 10
    assert "銀幣 x10" in gs.characters["pc_bram"].inventory
