"""Per-IP rate limiting (D13) — fixed-window counter in Redis. Abuse protection
without accounts. Limit is config (PALLET_API_RATE_LIMIT_PER_MIN)."""
from __future__ import annotations

import time

from fastapi import HTTPException, Request

from pallet_api.config import settings


async def enforce_rate_limit(request: Request) -> None:
    redis = request.app.state.redis
    ip = request.client.host if request.client else "unknown"
    window = int(time.time() // 60)
    key = f"rl:{ip}:{window}"
    n = await redis.incr(key)
    if n == 1:
        await redis.expire(key, 90)
    if n > settings.rate_limit_per_min:
        raise HTTPException(status_code=429, detail={"error": {
            "code": "rate_limited",
            "message": f"Rate limit of {settings.rate_limit_per_min} "
                       f"requests/min exceeded."}})
