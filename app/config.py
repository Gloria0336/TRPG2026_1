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
    # Cheap model for the post-narration entity-state extraction pass (§8.2).
    # Defaults to the intent model unless overridden in .env.
    model_extract: str = "openai/gpt-4o-mini"
    openrouter_app_url: str = "http://localhost:8000"
    openrouter_app_name: str = "AI Living World MVP"

    # Memory / continuity
    # When True (and AI is online), each narration is read by model_extract to pull
    # structured entity-state deltas (who left, who turned hostile…). Offline this is
    # skipped and only structured/engine deltas apply.
    entity_extraction_enabled: bool = True

    # Debounce for auto-registering NEW entities the AI mentions in prose: a brand-new
    # place/person must be named this many times before it becomes a real record. Stops
    # one-off background flavour from bloating the world, while letting genuinely recurring
    # elements persist. A player's explicit travel target bypasses this (threshold 1).
    mention_promote_threshold: int = 3

    # Web dashboard
    web_host: str = "127.0.0.1"
    web_port: int = 8000

    # Engine
    dice_seed: int | None = None
    narrate_context_window: int = 12
    ai_offline: bool = False

    # SQLite memory store (design §5.3, SQLite variant). Mutable so tests can point
    # it at a temp file; the DB layer reopens when this changes.
    db_path: Path = SAVE_DIR / "world.db"

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
