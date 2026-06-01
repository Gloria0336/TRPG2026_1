"""Single-process entrypoint.

Because there is no database, the Discord bot and the web dashboard must share the same
in-memory GameState — so both run on one asyncio loop in this one process. Start with:

    python -m app.run
"""
from __future__ import annotations

import asyncio

import uvicorn

from .ai import orchestrator
from .config import settings
from .discord_bot.bot import bot
from .state import game_state
from .web.app import app as web_app


async def _serve_web() -> None:
    config = uvicorn.Config(
        web_app,
        host=settings.web_host,
        port=settings.web_port,
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    # Resume a saved session so the dashboard isn't blank on restart (no DB).
    if game_state.get_state() is None:
        saved = game_state.GameState.load()
        if saved:
            game_state.set_state(saved)

    tasks = [asyncio.create_task(_serve_web(), name="web")]

    if settings.discord_token:
        tasks.append(asyncio.create_task(bot.start(settings.discord_token), name="discord"))
        print(f"[run] Discord bot + dashboard at http://{settings.web_host}:{settings.web_port}")
    else:
        print("[run] No DISCORD_TOKEN — dashboard only at "
              f"http://{settings.web_host}:{settings.web_port} (set DISCORD_TOKEN to enable play).")

    try:
        await asyncio.gather(*tasks)
    finally:
        await orchestrator.aclose()
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[run] shutting down.")
