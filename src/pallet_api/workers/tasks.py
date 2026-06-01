"""arq solve task. Runs the solve in a separate process (hard timeout) via a
thread so the worker's event loop stays free. Returns a status dict that arq
stores as the job result.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict

from pallet_api.config import settings
from pallet_api.solver.runner import run_with_hard_timeout


async def solve_job(ctx: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = settings.solver_cfg()
    return await asyncio.to_thread(
        run_with_hard_timeout, payload, cfg, settings.hard_budget_s)
