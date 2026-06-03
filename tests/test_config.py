from app.config import Settings


def test_discord_token_is_normalized():
    settings = Settings(discord_token=' "Bot abc.def.ghi" ')
    assert settings.discord_token == "abc.def.ghi"
