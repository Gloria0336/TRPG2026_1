"""FastAPI app for the read-only dashboard."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from ..ai import orchestrator
from ..config import STATIC_DIR, settings
from ..db import store
from ..state import game_state
from .portal_api import router as portal_router
from .session import PortalSessionMiddleware


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Ensure the SQLite memory store exists when the dashboard runs standalone.
    store.init_db()
    yield


app = FastAPI(title="AI Living World 儀表板", lifespan=_lifespan)

if settings.parsed_web_cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.parsed_web_cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        allow_credentials=True,
    )

app.add_middleware(
    PortalSessionMiddleware,
    secret_key=settings.portal_session_secret or "dev-only-change-me",
    same_site=settings.portal_cookie_samesite,
    https_only=settings.portal_cookie_secure,
)

app.include_router(portal_router)


async def _snapshot() -> dict:
    gs = game_state.get_state()
    snap = gs.dashboard_view() if gs else {"started": False, "version": -1}
    snap["ai"] = await orchestrator.health()
    return snap


@app.get("/api/state")
async def api_state() -> JSONResponse:
    return JSONResponse(await _snapshot())


@app.get("/api/ai/health")
async def api_ai_health() -> JSONResponse:
    return JSONResponse(await orchestrator.health(force=True))


@app.get("/api/stream")
async def api_stream(request: Request) -> EventSourceResponse:
    async def event_gen():
        last_key = None
        while True:
            if await request.is_disconnected():
                break
            snap = await _snapshot()
            gs = game_state.get_state()
            ai = snap.get("ai") or {}
            key = (id(gs), snap.get("version"), ai.get("status"), ai.get("checked_at"))
            if key != last_key:
                last_key = key
                yield {"event": "state", "data": json.dumps(snap, ensure_ascii=False)}
            await asyncio.sleep(2.0)

    return EventSourceResponse(event_gen())


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
