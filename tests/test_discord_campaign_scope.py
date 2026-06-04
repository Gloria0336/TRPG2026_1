from app.discord_bot import bot as discord_bot
from app.state import game_state


def test_discord_channel_allowlist_defaults_to_open(monkeypatch):
    monkeypatch.setattr(discord_bot.settings, "discord_allowed_channel_ids", "")

    assert discord_bot._is_allowed_channel_id(123)


def test_discord_channel_allowlist_rejects_other_channels(monkeypatch):
    monkeypatch.setattr(discord_bot.settings, "discord_allowed_channel_ids", "1511969579574755409")

    assert discord_bot._is_allowed_channel_id(1511969579574755409)
    assert not discord_bot._is_allowed_channel_id(123)
    assert "<#1511969579574755409>" in discord_bot._disallowed_channel_message()


def test_start_does_not_block_on_unbound_active_campaign():
    game_state.set_state(None)
    game_state.reset_state(channel_id=0)

    assert discord_bot._start_block_message(123) is None

    game_state.set_state(None)


def test_start_blocks_active_campaign_bound_to_any_real_channel():
    game_state.set_state(None)
    game_state.reset_state(channel_id=123)

    assert "這個頻道已經有" in discord_bot._start_block_message(123)
    assert "<#123>" in discord_bot._start_block_message(456)

    game_state.set_state(None)


def test_finish_targets_unbound_campaign_but_not_other_channel():
    game_state.set_state(None)
    unbound = game_state.reset_state(channel_id=0)

    gs, msg = discord_bot._finish_target_for_channel(123)
    assert gs is unbound
    assert msg is None

    bound = game_state.reset_state(channel_id=456)
    gs, msg = discord_bot._finish_target_for_channel(123)
    assert gs is None
    assert "<#456>" in msg

    gs, msg = discord_bot._finish_target_for_channel(456)
    assert gs is bound
    assert msg is None

    game_state.set_state(None)
