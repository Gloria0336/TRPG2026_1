"""Runtime configuration loaded from environment / .env.

All tunables live here so the rest of the code never reads os.environ directly.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = parent of the `app` package directory.
ROOT_DIR = Path(__file__).resolve().parent.parent
SAVE_DIR = ROOT_DIR / "save"
STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


class Settings(BaseSettings):
    """Typed settings; values come from the environment or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Discord
    discord_token: str = ""
    discord_guild_id: str = ""

    # OpenRouter / LLM
    openrouter_api_key: str = ""
    model_intent: str = "openai/gpt-4o-mini"
    model_narrate: str = "anthropic/claude-sonnet-4.5"
    openrouter_app_url: str = "http://localhost:8000"
    openrouter_app_name: str = "AI Living World MVP"

    # Web dashboard
    web_host: str = "127.0.0.1"
    web_port: int = 8000

    # Engine
    dice_seed: int | None = None
    narrate_context_window: int = 12
    ai_offline: bool = False

    @field_validator("dice_seed", mode="before")
    @classmethod
    def blank_dice_seed_means_random(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @property
    def session_path(self) -> Path:
        return SAVE_DIR / "session.json"


settings = Settings()

# Make sure the save directory exists at import time (no DB; JSON snapshots live here).
SAVE_DIR.mkdir(parents=True, exist_ok=True)
