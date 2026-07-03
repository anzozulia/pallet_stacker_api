"""Request-body size cap (hardening plan C2/F6) — pure ASGI middleware.

Unbounded bodies amplify: a 50 MB request transits uvicorn buffering, pydantic
parsing, a Redis round-trip, and a pickle into the solve subprocess, costing
several hundred MB of transient RAM. This middleware rejects oversized bodies
with a `413` in the standard error envelope, before FastAPI parses anything.

Two layers: an honest `Content-Length` is rejected up front without reading
the body; a missing or lying header is caught by counting the actual received
bytes (chunked uploads included). The counting path raises an HTTPException —
NOT a custom exception — because FastAPI's body-read wrapper catches generic
exceptions and remaps them to a plain 400 ("error parsing the body") while
re-raising HTTPExceptions untouched; the app's registered handler then formats
the envelope. The limit is read from the settings OBJECT per request (tests
monkeypatch the attribute); the env var itself is parsed once at import.
"""
from __future__ import annotations

import json

from starlette.exceptions import HTTPException as StarletteHTTPException

from pallet_api.config import settings


def _envelope(max_bytes: int) -> dict:
    return {"error": {
        "code": "payload_too_large",
        "message": f"Request body exceeds the "
                   f"{max_bytes // (1024 * 1024)} MB limit."}}


class BodySizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        max_bytes = int(settings.max_body_bytes)

        declared = None
        for k, v in scope.get("headers") or []:
            if k == b"content-length":
                try:
                    declared = int(v)
                except ValueError:
                    pass
                break
        if declared is not None and declared > max_bytes:
            await self._send_413(send, max_bytes)
            return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > max_bytes:
                    raise StarletteHTTPException(status_code=413,
                                                 detail=_envelope(max_bytes))
            return message

        await self.app(scope, limited_receive, send)

    @staticmethod
    async def _send_413(send, max_bytes: int) -> None:
        body = json.dumps(_envelope(max_bytes)).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})
