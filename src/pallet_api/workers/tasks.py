"""arq solve task. Runs the solve in a separate process (hard timeout) via a
thread so the worker's event loop stays free. Returns a status dict that arq
stores as the job result.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict

from pallet_api.config import settings
from pallet_api.solver.runner import run_with_hard_timeout

log = logging.getLogger("pallet_api.worker")


async def solve_job(ctx: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = settings.solver_cfg()
    n = len(payload.get("boxes", []))
    log.info("solve_start job_id=%s n_boxes=%d", ctx.get("job_id"), n)
    t0 = time.perf_counter()
    out = await asyncio.to_thread(
        run_with_hard_timeout, payload, cfg, settings.hard_budget_s)
    log.info("solve_end job_id=%s n_boxes=%d status=%s runtime_s=%.1f",
             ctx.get("job_id"), n, out.get("status"), time.perf_counter() - t0)
    return out
