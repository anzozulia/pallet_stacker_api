"""Hard-timeout runner tests (D6) — the subprocess kill path. Needs the core."""
from __future__ import annotations

import pytest

pytest.importorskip("pallet_packer")

from pallet_api.solver.runner import run_with_hard_timeout  # noqa: E402


def test_hard_timeout_kills_and_reports_timeout(fast_cfg, pack_payload):
    # A real solve takes seconds (spawn + import + BRKGA); a 0.1s hard cap can
    # never be met, so it must be killed and reported as timeout.
    out = run_with_hard_timeout(pack_payload(5, budget=5), fast_cfg,
                                hard_timeout_s=0.1)
    assert out["status"] == "timeout"
    assert out["error"]["code"] == "solve_timeout"


def test_done_returns_result(fast_cfg, pack_payload):
    out = run_with_hard_timeout(pack_payload(4, budget=5), fast_cfg,
                                hard_timeout_s=60)
    assert out["status"] == "done"
    assert "input_summary" in out["result"]


def test_gate_violation_maps_to_invalid_input(fast_cfg, pack_payload):
    out = run_with_hard_timeout(pack_payload(2, bad=True), fast_cfg,
                                hard_timeout_s=60)
    assert out["status"] == "failed"
    assert out["error"]["code"] == "invalid_input"
    assert out["error"]["problems"]


def test_non_gate_exception_maps_to_solver_error(fast_cfg):
    # Missing 'pallet' -> KeyError inside solve (not a PackingInputError).
    out = run_with_hard_timeout({"boxes": [{"id": "B1", "length": 10,
                                            "width": 10, "height": 10}]},
                                fast_cfg, hard_timeout_s=60)
    assert out["status"] == "failed"
    assert out["error"]["code"] == "solver_error"
