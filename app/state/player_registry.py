"""Persistent Discord-user character cards for the player portal and /join flow."""
from __future__ import annotations

import json

from ..config import SAVE_DIR
from ..engine.types import Character
from ..logging_setup import get_logger

log = get_logger("player_registry")

REGISTRY_PATH = SAVE_DIR / "player_characters.json"


def _load_raw() -> dict:
    if not REGISTRY_PATH.exists():
        return {"players": {}}
    try:
        data = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("player registry load failed (%s): %s", type(exc).__name__, exc)
        return {"players": {}}
    if not isinstance(data, dict):
        return {"players": {}}
    players = data.get("players")
    if not isinstance(players, dict):
        data["players"] = {}
    return data


def _save_raw(data: dict) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_character(user_id: str | int | None) -> Character | None:
    """Return the user's latest registered character card, if any."""
    if user_id is None:
        return None
    raw = _load_raw().get("players", {}).get(str(user_id))
    if not isinstance(raw, dict):
        return None
    try:
        return Character.from_dict(raw["character"])
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("player registry record invalid for user=%s (%s): %s", user_id, type(exc).__name__, exc)
        return None


def set_character(user_id: str | int, character: Character) -> None:
    """Bind a character card to a Discord user id."""
    data = _load_raw()
    players = data.setdefault("players", {})
    players[str(user_id)] = {"character": character.to_dict()}
    _save_raw(data)
