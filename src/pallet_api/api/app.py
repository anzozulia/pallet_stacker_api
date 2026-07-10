"""FastAPI application factory. The API tier is stateless; all shared state
(queue, job status, results) lives in Redis via an arq pool.

The app is heavily documented so `/docs` (Swagger UI), `/redoc`, and
`/openapi.json` are a complete, self-service reference — including a top-level
guide, per-field descriptions, every response code, and a uniform error envelope.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from pallet_api.config import settings
from pallet_api.api.bodylimit import BodySizeLimitMiddleware
from pallet_api.api.limits import RateLimitMiddleware
from pallet_api.api.routes import router

DESCRIPTION = """
A free, open, no-login HTTP API for **3D pallet / container loading**. Submit a
list of boxes and a target pallet; get back a physically valid packing plan —
where each box goes, in which orientation, on which pallet — computed by the
verified `pallet_packer` BRKGA engine.

### How it works (asynchronous)
A large solve can take up to the time budget (~90 s by default), so packing is a
**job** (small inputs finish in well under a second):
1. **`POST /pack`** → validates your request and returns `202` with a `job_id`.
2. **`GET /jobs/{job_id}`** → poll (~1 s): `queued` → `running` → `done` (with
   `result`), or `failed` / `timeout`.

### Input contract
- Box & pallet **spatial dimensions must be positive integers** in any single
  consistent unit (mm, cm, inch — your choice). Non-integer dims are rejected, not rounded.
- Weights may be fractional. Box ids must be unique. At most the box cap per
  request (**500** by default; configurable).
- Omit a cap (`max_weight`, `max_load_on_top`) to mean *unlimited* (omitting and
  sending `null` are equivalent).

### Constraints supported
Weight cap, fragility (`max_load_on_top`), base **support ratio**, **group**
co-location (same group → same pallet), `max_pallets`, and pallet `max_overhang`.
Fragility caps stacked *weight* only — orientation-sensitive goods also need
`rotations: "this_side_up"` to stay upright.

### Limits & guarantees
- **Time budget:** ~90 s soft (the solver's own budget) with a hard ~120 s
  wall-clock kill at the worker → `timeout` (a runaway solve never pins a worker).
  Both are configurable defaults.
- **Determinism:** same input + same `seed` ⇒ identical plan (on symmetric loads
  different seeds may tie on the same plan — expected, not a bug).
- **Body cap:** requests over 10 MB (configurable) → `413`.
- **Rate limit:** per-IP, a fixed 60-second window — up to 30 requests/window by
  default (configurable); `429` over the limit.
- **No accounts, no history:** results are ephemeral — fetchable for the result
  TTL (~1 h by default) after completion, then `404`.

### Errors
Every `4xx`/`5xx` response uses **one envelope**:
`{"error": {"code", "message"?, "problems"?}}`.

### Quality (honest)
Packings are physically valid but not world-class density (~1.7 pp behind 2013
full-support academic SOTA) — appropriate for a free, general-purpose tool.
"""

TAGS_METADATA = [
    {"name": "packing", "description": "Submit packing jobs and fetch results."},
    {"name": "operational", "description": "Health and version probes."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        yield
    finally:
        await app.state.redis.aclose()


_CODE_BY_STATUS = {404: "not_found", 405: "method_not_allowed",
                   413: "payload_too_large", 429: "rate_limited",
                   500: "internal_error", 503: "degraded"}


async def _http_exc_handler(request: Request, exc: StarletteHTTPException):
    """Normalise every HTTPException to the uniform `{"error": {...}}` envelope."""
    d = exc.detail
    if isinstance(d, dict) and "error" in d:
        body = d                                   # already enveloped (our routes)
    elif isinstance(d, dict) and "code" in d:
        body = {"error": d}
    else:
        body = {"error": {"code": _CODE_BY_STATUS.get(exc.status_code, "error"),
                          "message": d if isinstance(d, str) else None}}
    return JSONResponse(status_code=exc.status_code, content=body,
                        headers=getattr(exc, "headers", None))


async def _validation_exc_handler(request: Request, exc: RequestValidationError):
    """Wrap pydantic request-validation errors in the same envelope (code 422)."""
    problems = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": {
        "code": "validation_error",
        "message": "Request body failed schema validation.",
        "problems": problems}})


def create_app() -> FastAPI:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = FastAPI(
        title="pallet-packer-api",
        version=settings.version,
        summary="Asynchronous 3D pallet / container loading over HTTP.",
        description=DESCRIPTION,
        license_info={"name": "MIT"},
        openapi_tags=TAGS_METADATA,
        servers=[{"url": "/", "description": "This server (relative to the host you reached)."}],
        lifespan=lifespan,
    )
    app.add_exception_handler(StarletteHTTPException, _http_exc_handler)
    app.add_exception_handler(RequestValidationError, _validation_exc_handler)
    # Middleware order (add_middleware is LIFO — last added runs first):
    # CORS OUTERMOST so the browser preflight (OPTIONS) is answered before
    # the rate limiter sees it and so 429/413 responses still carry the
    # Access-Control-* headers the front-end needs to READ the error; then
    # the rate limiter, deciding a 429 before any body byte is received
    # (round 5, R4 — CORS never reads the body, so that property holds);
    # then the body cap; then the app.
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(RateLimitMiddleware)
    if settings.cors_origins:
        # Empty PALLET_API_CORS_ORIGINS= (explicit opt-out) = middleware not
        # installed at all — the round-5 server-to-server posture. The default
        # is ["*"]: the static front-end calls this API directly from the
        # browser, and a cross-origin POST /pack would otherwise be blocked
        # (the UI then shows "Couldn't reach the packing service"). No
        # cookies/credentials are used, so "*" is safe; lock down via env.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
            max_age=3600,
        )

    @app.get("/", include_in_schema=False)
    async def _root():
        return RedirectResponse(url="/docs")

    app.include_router(router, prefix="/api/v1")
    return app


app = create_app()
