"""Per-IP rate limiting (D13) — fixed-window counters in Redis.

Round 5 (R4): enforcement moved from a route DEPENDENCY into pure-ASGI
middleware so it runs BEFORE the request body is received or parsed —
previously a client could stream a full 10 MB body (and pay the JSON parse
on the event loop) before the 429 fired, and rejected-oversize (413)
attempts never counted. The middleware also adds a separate, generous
counter for result polling (GET /jobs/*), which used to be uncapped.

Limits are config: PALLET_API_RATE_LIMIT_PER_MIN (POST /pack, default 30)
and PALLET_API_POLL_RATE_LIMIT_PER_MIN (GET /jobs/*, default 600).
/health, /version and /docs are never limited (Docker healthchecks and
humans reading the docs must not consume quota).
"""
from __future__ import annotations

import json
import time

from fastapi import HTTPException, Request
from redis.exceptions import RedisError

from pallet_api.config import settings

_PACK_PATH = "/api/v1/pack"
_JOBS_PREFIX = "/api/v1/jobs/"


async def _check_limit(redis, ip: str, prefix: str, limit: int) -> None:
    """Increment the fixed-window counter; raise 429/503 HTTPException on
    limit exceeded / Redis down (same envelopes as always)."""
    window = int(time.time() // 60)
    key = f"{prefix}:{ip}:{window}"
    try:
        n = await redis.incr(key)
        if n == 1:
            await redis.expire(key, 90)
    except RedisError:
        # Fail closed with the same degraded envelope as /pack's own queue
        # path (C8/F13) — a bare 500 outside the envelope would leak the
        # limiter as an internal error while Redis is down anyway.
        raise HTTPException(status_code=503, detail={"error": {
            "code": "degraded",
            "message": "Queue unavailable; try again shortly."}})
    if n > limit:
        raise HTTPException(status_code=429, detail={"error": {
            "code": "rate_limited",
            "message": f"Rate limit of {limit} requests/min exceeded."}})


async def enforce_rate_limit(request: Request) -> None:
    """The /pack limiter as a callable (kept for direct use/tests; the
    middleware below is the production enforcement point)."""
    ip = request.client.host if request.client else "unknown"
    await _check_limit(request.app.state.redis, ip, "rl",
                       settings.rate_limit_per_min)


class RateLimitMiddleware:
    """Pure-ASGI limiter, registered OUTSIDE the body-cap middleware so it
    runs first: a 429 is decided from the scope alone, before a single
    body byte is received."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        method = scope.get("method", "")
        prefix = limit = None
        if method == "POST" and path == _PACK_PATH:
            prefix, limit = "rl", settings.rate_limit_per_min
        elif method == "GET" and path.startswith(_JOBS_PREFIX):
            prefix, limit = "rlp", settings.poll_rate_limit_per_min
        if prefix is None:
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        ip = client[0] if client else "unknown"
        redis = scope["app"].state.redis
        try:
            await _check_limit(redis, ip, prefix, int(limit))
        except HTTPException as exc:
            # Outermost middleware: FastAPI's exception handlers are inside
            # us, so format the envelope directly (mirrors bodylimit.py).
            body = json.dumps(exc.detail).encode()
            await send({"type": "http.response.start",
                        "status": exc.status_code,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length",
                                     str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
