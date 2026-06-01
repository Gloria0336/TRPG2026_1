"""FastAPI app for the read-only dashboard.

Shares the in-process GameState with the Discord bot (no DB). Live updates are pushed
over Server-Sent Events; because there is no database, the SSE loop simply observes the
shared in-memory object and emits a fresh snapshot whenever its version changes.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

from ..config import STATIC_DIR
from ..state import game_state

app = FastAPI(title="AI Living World — Dashboard")


def _snapshot() -> dict:
    gs = game_state.get_state()
    return gs.dashboard_view() if gs else {"started": False, "version": -1}


@app.get("/api/state")
async def api_state() -> JSONResponse:
    return JSONResponse(_snapshot())


@app.get("/api/stream")
async def api_stream(request: Request) -> EventSourceResponse:
    async def event_gen():
        last_key = None
        while True:
            if await request.is_disconnected():
                break
            gs = game_state.get_state()
            snap = gs.dashboard_view() if gs else {"started": False, "version": -1}
            key = (id(gs), snap.get("version"))
            if key != last_key:
                last_key = key
                yield {"event": "state", "data": json.dumps(snap, ensure_ascii=False)}
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_gen())


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# Static assets (app.js, style.css).
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
