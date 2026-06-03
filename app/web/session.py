"""Small signed-cookie session middleware for the player portal."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class PortalSessionMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        secret_key: str,
        cookie_name: str = "trpg_portal_session",
        same_site: str = "lax",
        https_only: bool = False,
        max_age: int = 60 * 60 * 24 * 14,
    ) -> None:
        self.app = app
        self.secret_key = secret_key.encode("utf-8")
        self.cookie_name = cookie_name
        self.same_site = same_site
        self.https_only = https_only
        self.max_age = max_age

    def _sign(self, data: bytes) -> str:
        return hmac.new(self.secret_key, data, hashlib.sha256).hexdigest()

    def _decode(self, value: str | None) -> dict[str, Any]:
        if not value or "." not in value:
            return {}
        payload_b64, signature = value.rsplit(".", 1)
        try:
            payload = base64.urlsafe_b64decode(payload_b64.encode("ascii"))
        except Exception:
            return {}
        if not hmac.compare_digest(self._sign(payload), signature):
            return {}
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}

    def _encode(self, session: dict[str, Any]) -> str:
        payload = json.dumps(session, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload).decode("ascii")
        return f"{payload_b64}.{self._sign(payload)}"

    def _cookie_header(self, value: str) -> str:
        parts = [
            f"{self.cookie_name}={value}",
            f"Max-Age={self.max_age}",
            "Path=/",
            f"SameSite={self.same_site}",
            "HttpOnly",
        ]
        if self.https_only:
            parts.append("Secure")
        return "; ".join(parts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        cookies = {}
        for key, value in scope.get("headers", []):
            if key == b"cookie":
                for part in value.decode("latin-1").split(";"):
                    if "=" in part:
                        name, raw = part.strip().split("=", 1)
                        cookies[name] = raw
        scope["session"] = self._decode(cookies.get(self.cookie_name))

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                session = scope.get("session") or {}
                if session:
                    headers.append("Set-Cookie", self._cookie_header(self._encode(session)))
                elif cookies.get(self.cookie_name):
                    headers.append("Set-Cookie", self._cookie_header("") + "; Expires=Thu, 01 Jan 1970 00:00:00 GMT")
            await send(message)

        await self.app(scope, receive, send_wrapper)
