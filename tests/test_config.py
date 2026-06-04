from app.config import Settings


def test_discord_token_is_normalized():
    settings = Settings(discord_token=' "Bot abc.def.ghi" ')
    assert settings.discord_token == "abc.def.ghi"


def test_discord_allowed_channel_ids_are_parsed():
    settings = Settings(discord_allowed_channel_ids=" 1511969579574755409, 42 ")
    assert settings.parsed_discord_allowed_channel_ids == {1511969579574755409, 42}
