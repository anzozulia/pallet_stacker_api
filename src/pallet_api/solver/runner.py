"""Hard wall-clock enforcement (D6). The core solve is CPU-bound sync code that
asyncio cannot interrupt, so it runs in a separate PROCESS that we terminate on
timeout. This is the only reliable hard-kill.
"""
from __future__ import annotations

import multiprocessing as mp
import queue as _queue
from typing import Any, Dict

_CTX = mp.get_context("spawn")   # clean process; picklable target + args


def _target(payload: Dict[str, Any], cfg: Dict[str, Any], q) -> None:
    try:
        from pallet_api.solver.adapter import solve
        q.put(("done", solve(payload, cfg)))
    except Exception as e:  # noqa: BLE001 — report, don't crash silently
        from pallet_packer import PackingInputError
        if isinstance(e, PackingInputError):
            q.put(("invalid", list(e.problems)))
        else:
            q.put(("error", f"{type(e).__name__}: {e}"))


def run_with_hard_timeout(payload: Dict[str, Any], cfg: Dict[str, Any],
                          hard_timeout_s: float) -> Dict[str, Any]:
    """Run solve in a subprocess; kill it past hard_timeout_s. Returns a status
    dict: {status: done|timeout|failed, result?|error?}."""
    q = _CTX.Queue()
    p = _CTX.Process(target=_target, args=(payload, cfg, q), daemon=True)
    p.start()
    try:
        kind, val = q.get(timeout=hard_timeout_s)
    except _queue.Empty:
        if p.is_alive():
            p.terminate(); p.join(3)
            if p.is_alive():
                p.kill(); p.join(2)
            return {"status": "timeout", "error": {
                "code": "solve_timeout",
                "message": f"Exceeded the {hard_timeout_s:g}s wall-clock limit."}}
        return {"status": "failed", "error": {
            "code": "solver_crashed",
            "message": "solver process exited without a result"}}
    finally:
        if p.is_alive():
            p.join(2)
    if kind == "done":
        return {"status": "done", "result": val}
    if kind == "invalid":
        return {"status": "failed", "error": {"code": "invalid_input", "problems": val}}
    return {"status": "failed", "error": {"code": "solver_error", "message": val}}
