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
from .logging_setup import close_logging, get_logger, setup_logging

setup_logging()
log = get_logger("run")

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

    log.info("startup: ai_offline=%s has_key=%s model_intent=%s model_narrate=%s",
             settings.ai_offline, bool(settings.openrouter_api_key),
             settings.model_intent, settings.model_narrate)

    if settings.discord_token:
        tasks.append(asyncio.create_task(bot.start(settings.discord_token), name="discord"))
        log.info("Discord bot + dashboard at http://%s:%s", settings.web_host, settings.web_port)
    else:
        log.warning("No DISCORD_TOKEN — dashboard only at http://%s:%s (set DISCORD_TOKEN to enable play).",
                    settings.web_host, settings.web_port)

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
        log.info("shutdown requested by Ctrl+C")
        print("\n[run] shutting down.")
    finally:
        close_logging()
