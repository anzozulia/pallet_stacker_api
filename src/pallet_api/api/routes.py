"""HTTP routes — see docs/02_api_contract.md. Every route is fully documented
(summary, description, response model, error responses) so the Swagger UI at
`/docs` and the OpenAPI schema are a complete, self-service reference."""
from __future__ import annotations

import logging

from arq.jobs import Job, JobStatus
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from pallet_packer import PackingInputError

from pallet_api.config import settings
from pallet_api.api.limits import enforce_rate_limit
from pallet_api.schemas import (PackRequest, JobAccepted, JobState, ErrorEnvelope,
                                HealthState, VersionInfo)
from pallet_api.solver.adapter import validate_request

router = APIRouter()
log = logging.getLogger("pallet_api")

PACKING = "packing"
OPERATIONAL = "operational"


def _err(code: str, message: str | None, problems=None) -> dict:
    """Build the uniform error envelope: {"error": {code, message?, problems?}}."""
    body: dict = {"code": code, "message": message}
    if problems is not None:
        body["problems"] = problems
    return {"error": body}


# Reusable OpenAPI error-response declarations (documentation; the runtime bodies
# come from the handlers here + the global error handlers in app.py).
def _resp(model, description, example):
    return {"model": model, "description": description,
            "content": {"application/json": {"example": example}}}


_RESP_400 = _resp(ErrorEnvelope, "Input failed the packing contract (the core gate). "
                  "Nothing is queued.",
                  _err("invalid_input", "Request did not satisfy the packing input contract.",
                       ["box[3] (id='B0003').length must be a positive integer (got 100.5)"]))
_RESP_422 = _resp(ErrorEnvelope, "Request body failed schema validation (wrong type, "
                  "missing field, non-integer dimension, or a value out of bounds).",
                  _err("validation_error", "Request body failed schema validation.",
                       ["body.boxes.0.length: Input should be a valid integer"]))
_RESP_422_PATH = _resp(ErrorEnvelope, "A path parameter failed validation.",
                       _err("validation_error", "Request failed schema validation.",
                            ["path.job_id: invalid value"]))
_RESP_503 = _resp(ErrorEnvelope, "Redis (queue + result store) is unreachable; the "
                  "request could not be served.",
                  _err("degraded", "Queue unavailable; try again shortly.", None))
_RESP_429 = _resp(ErrorEnvelope, "Per-IP rate limit exceeded (a fixed 60-second window; "
                  "default 30 requests, configurable). Retry in the next window.",
                  _err("rate_limited", "Rate limit of 30 requests/min exceeded.", None))
_RESP_404 = _resp(ErrorEnvelope, "Unknown or expired job id (results are ephemeral — "
                  "no history).", _err("not_found", "unknown or expired job_id", None))


@router.get("/health", tags=[OPERATIONAL], summary="Liveness / readiness probe",
            response_model=HealthState, response_description="Service is up; Redis reachable.",
            responses={503: _RESP_503},
            description="Returns `200` when the service is up and Redis is reachable, "
                        "else `503`. Suitable for container/orchestrator health checks.")
async def health(request: Request):
    try:
        await request.app.state.redis.ping()
        return {"status": "ok", "redis": "ok"}
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=_err("degraded", "Redis is unreachable."))


@router.get("/version", tags=[OPERATIONAL], summary="Service + core versions",
            response_model=VersionInfo, response_description="Service, core, and API versions.",
            description="This service's version, the pinned `pallet_packer` core "
                        "version, and the API contract version.")
async def version():
    return {"service": settings.version, "core": settings.core_version, "api": "v1"}


@router.post("/pack", tags=[PACKING], status_code=202, response_model=JobAccepted,
             dependencies=[Depends(enforce_rate_limit)],
             summary="Submit a packing job",
             response_description="Job accepted and queued; poll `links.self`.",
             responses={400: _RESP_400, 422: _RESP_422, 429: _RESP_429, 503: _RESP_503},
             description="""
Validate a packing request and **enqueue** it — returns `202` with a `job_id`
immediately. It does **not** solve inline (large solves can take up to the time
budget, ~90 s by default; small inputs finish in well under a second).

**Next step:** poll `GET /jobs/{job_id}` until `status` is terminal
(`done` | `failed` | `timeout`).

**Two validation layers** (both reject before anything is queued):
- **`422 validation_error`** — the request body failed the JSON schema: wrong
  type, a missing field, a **non-integer** dimension, or a value out of bounds
  (e.g. a non-positive dimension, `support_ratio` outside [0,1], `max_pallets < 1`).
- **`400 invalid_input`** — the body is well-shaped but breaks a packing
  **contract** rule that only the core gate can check: a duplicate box `id`, or
  more than the box cap (default **500**, configurable). `problems[]` lists each.

Omit a cap (`max_weight`, `max_load_on_top`) to mean *unlimited*. `503` if the
queue (Redis) is unreachable.
""")
async def pack(req: PackRequest, request: Request):
    payload = req.model_dump()
    # The contract rules only the gate can check (unique ids, box cap, …). Reject up
    # front; nothing is queued.
    try:
        validate_request(payload, max_boxes=settings.max_boxes)
    except PackingInputError as e:
        return JSONResponse(status_code=400, content=_err(
            "invalid_input", "Request did not satisfy the packing input contract.",
            list(e.problems)))
    try:
        job = await request.app.state.redis.enqueue_job("solve_job", payload)
    except RedisError:
        raise HTTPException(status_code=503, detail=_err(
            "degraded", "Queue unavailable; try again shortly."))
    log.info("job_submitted job_id=%s n_boxes=%d max_pallets=%s",
             job.job_id, len(payload["boxes"]),
             payload.get("options", {}).get("max_pallets"))
    return {"job_id": job.job_id, "status": "queued",
            "links": {"self": f"/api/v1/jobs/{job.job_id}"}}


@router.get("/jobs/{job_id}", tags=[PACKING], summary="Poll a job / fetch its result",
            response_model=JobState, response_model_exclude_none=True,
            response_description="The job's current state (and `result` once `done`).",
            responses={404: _RESP_404, 422: _RESP_422_PATH, 503: _RESP_503},
            description="""
Poll a submitted job. Status progression:
`queued` → `running` → terminal (`done` | `failed` | `timeout`). A recommended
poll interval is ~1 s (fast jobs may go straight from `queued` to a terminal
state without an observable `running`).

- `done` → `result` holds the full packing plan (see the `PackResult` schema).
- `failed` / `timeout` → `error` explains why (the code is in `error.code`).
- Unknown or expired id → `404`. A `done` result stays fetchable for the result
  TTL (~1 h by default) and then returns `404` — there is no history.

Optional null fields (`result`/`error`/`meta`) are omitted from the response.
`503` if the result store (Redis) is unreachable.
""")
async def job_state(
        request: Request,
        job_id: str = Path(..., description="Opaque job id returned by `POST /pack` as "
                           "`job_id` / `links.self`.",
                           examples=["pk_3f8a1c9e4b7d4a2e9c1f"])):
    j = Job(job_id, request.app.state.redis)
    try:
        st = await j.status()
    except RedisError:
        raise HTTPException(status_code=503, detail=_err(
            "degraded", "Result store unavailable; try again shortly."))
    if st == JobStatus.not_found:
        raise HTTPException(status_code=404,
                            detail=_err("not_found", "unknown or expired job_id"))
    if st in (JobStatus.deferred, JobStatus.queued):
        return {"job_id": job_id, "status": "queued"}
    if st == JobStatus.in_progress:
        return {"job_id": job_id, "status": "running"}
    # complete — the task returned a status dict
    try:
        outcome = await j.result(timeout=2)
    except Exception as e:  # noqa: BLE001 — task raised / arq-level timeout
        return {"job_id": job_id, "status": "failed",
                "error": {"code": "solver_failed", "message": str(e)[:300]}}
    resp = {"job_id": job_id, "status": outcome.get("status", "failed")}
    if outcome.get("status") == "done":
        resp["result"] = outcome["result"]
    else:
        resp["error"] = outcome.get("error")
    return resp
