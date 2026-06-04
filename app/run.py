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
from .single_instance import AlreadyRunningError, acquire as acquire_instance_lock

setup_logging()
log = get_logger("run")

from .db import store
from .discord_bot.bot import bot
from .state import campaigns, game_state
from .web.app import app as web_app
from .web.portal_app import app as portal_app


async def _serve_app(app, *, host: str, port: int) -> None:
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def _serve_web() -> None:
    await _serve_app(web_app, host=settings.web_host, port=settings.web_port)


async def _serve_portal() -> None:
    await _serve_app(portal_app, host=settings.portal_host, port=settings.portal_port)


async def main() -> None:
    # Refuse to start a second instance: two bots on one token fight over the
    # gateway session, so player actions get split between processes and the
    # active recording can look empty or incomplete.
    try:
        acquire_instance_lock()
    except AlreadyRunningError as exc:
        log.error("another app instance is already running (pid=%s) — refusing to start a duplicate. "
                  "Stop it first, or check that you didn't launch the app twice.", exc.pid)
        print(f"\n[run] 已有另一個實例在執行中 (pid={exc.pid})，拒絕重複啟動。請先關閉它再重跑。")
        return

    # Open the latest per-campaign SQLite store (entities / event history / summaries),
    # migrating a pre-refactor single save into a campaign directory on first run.
    campaigns.migrate_legacy_if_needed()
    if campaigns.resume_latest() is None:
        store.init_db()

    # Resume a saved session so the dashboard isn't blank on restart.
    if game_state.get_state() is None:
        saved = game_state.GameState.load()
        if saved:
            game_state.set_state(saved)

    tasks = [
        asyncio.create_task(_serve_web(), name="web"),
        asyncio.create_task(_serve_portal(), name="portal"),
    ]

    log.info("startup: ai_offline=%s has_key=%s model_intent=%s model_narrate=%s",
             settings.ai_offline, bool(settings.openrouter_api_key),
             settings.model_intent, settings.model_narrate)

    if settings.discord_token:
        tasks.append(asyncio.create_task(bot.start(settings.discord_token), name="discord"))
        log.info("Discord bot + dashboard at http://%s:%s", settings.web_host, settings.web_port)
        log.info("Player portal at http://%s:%s", settings.portal_host, settings.portal_port)
    else:
        log.warning(
            "No DISCORD_TOKEN — web only: dashboard at http://%s:%s, player portal at http://%s:%s "
            "(set DISCORD_TOKEN to enable play).",
            settings.web_host, settings.web_port, settings.portal_host, settings.portal_port,
        )

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
