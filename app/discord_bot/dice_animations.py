"""Discord dice animation asset lookup.

Change ACTIVE_DICE_ANIMATION_SET_ID to switch the bot to another dice GIF set.
The set folder must live under app/static/dice_rituals/<set_id>/ and contain
result_01.gif through result_20.gif.
"""
from __future__ import annotations

from pathlib import Path

import discord


ACTIVE_DICE_ANIMATION_SET_ID = "arcane_dice"
DICE_RITUALS_DIR = Path(__file__).resolve().parents[1] / "static" / "dice_rituals"


def animation_path(natural: int, set_id: str = ACTIVE_DICE_ANIMATION_SET_ID) -> Path | None:
    if not 1 <= natural <= 20:
        return None
    path = DICE_RITUALS_DIR / set_id / f"result_{natural:02d}.gif"
    return path if path.is_file() and path.stat().st_size > 0 else None


def animation_file(natural: int, set_id: str = ACTIVE_DICE_ANIMATION_SET_ID) -> discord.File | None:
    path = animation_path(natural, set_id)
    if path is None:
        return None
    return discord.File(path, filename=path.name)
