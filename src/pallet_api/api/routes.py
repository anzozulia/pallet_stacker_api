"""HTTP routes — see docs/02_api_contract.md."""
from __future__ import annotations

import logging

from arq.jobs import Job, JobStatus
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from pallet_packer import PackingInputError

from pallet_api.config import settings
from pallet_api.api.limits import enforce_rate_limit
from pallet_api.schemas import PackRequest
from pallet_api.solver.adapter import validate_request

router = APIRouter()
log = logging.getLogger("pallet_api")


@router.get("/health")
async def health(request: Request):
    try:
        await request.app.state.redis.ping()
        return {"status": "ok", "redis": "ok"}
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=503,
                            detail={"status": "degraded", "redis": "unreachable"})


@router.get("/version")
async def version():
    return {"service": settings.version, "api": "v1"}


@router.post("/pack", status_code=202, dependencies=[Depends(enforce_rate_limit)])
async def pack(req: PackRequest, request: Request):
    payload = req.model_dump()
    # The input contract (positive-integer dims, <=max_boxes, finite caps, …) is
    # enforced by the core gate. Reject up front; nothing is queued.
    try:
        validate_request(payload, max_boxes=settings.max_boxes)
    except PackingInputError as e:
        return JSONResponse(status_code=400, content={"error": {
            "code": "invalid_input",
            "message": "Request did not satisfy the packing input contract.",
            "problems": list(e.problems)}})
    job = await request.app.state.redis.enqueue_job("solve_job", payload)
    log.info("job_submitted job_id=%s n_boxes=%d max_pallets=%s",
             job.job_id, len(payload["boxes"]),
             payload.get("options", {}).get("max_pallets"))
    return {"job_id": job.job_id, "status": "queued",
            "links": {"self": f"/api/v1/jobs/{job.job_id}"}}


@router.get("/jobs/{job_id}")
async def job_state(job_id: str, request: Request):
    j = Job(job_id, request.app.state.redis)
    st = await j.status()
    if st == JobStatus.not_found:
        raise HTTPException(status_code=404, detail={"error": {
            "code": "not_found", "message": "unknown or expired job_id"}})
    if st in (JobStatus.deferred, JobStatus.queued):
        return {"job_id": job_id, "status": "queued"}
    if st == JobStatus.in_progress:
        return {"job_id": job_id, "status": "running"}
    # complete — the task returned a status dict
    try:
        outcome = await j.result(timeout=2)
    except Exception as e:  # noqa: BLE001 — task raised / arq-level timeout
        return {"job_id": job_id, "status": "failed", "error": {
            "code": "solver_failed", "message": str(e)[:300]}}
    resp = {"job_id": job_id, "status": outcome.get("status", "failed")}
    if outcome.get("status") == "done":
        resp["result"] = outcome["result"]
    else:
        resp["error"] = outcome.get("error")
    return resp
