"""Public player portal app.

This app is intentionally narrower than the GM dashboard app. It serves the
player portal static files and `/api/portal/*`, but does not mount dashboard
state, stream, or AI-health endpoints.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from ..config import PORTAL_DIR, settings
from ..db import store
from .portal_api import router as portal_router
from .session import PortalSessionMiddleware


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Ensure the SQLite memory store exists when the portal runs standalone.
    store.init_db()
    yield


app = FastAPI(title="AI Living World 玩家入口", lifespan=_lifespan)

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
app.mount("/", StaticFiles(directory=str(PORTAL_DIR), html=True), name="portal")
