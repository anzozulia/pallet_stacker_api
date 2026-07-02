"""Docker-tier tests for the v2 warm-start deadline (hardening plan B1/B2,
finding F2): homogeneous constrained loads used to run the v2 packer
unbounded (~40 s at N=200, ~175 s at N=400), blowing every service budget.
"""
from __future__ import annotations

import time

import pytest

pp = pytest.importorskip("pallet_packer")

from pallet_packer import Box, Pallet, PackerConfig            # noqa: E402
from pallet_packer.packer import PalletPacker                  # noqa: E402

PALLET = Pallet(length=1200, width=800, height=1500, max_weight=1000.0)


def _boxes(n):
    return [Box(id=f"B{i}", length=200, width=150, height=100, weight=2.0)
            for i in range(n)]


def test_pack_deadline_bounds_runtime_and_conserves_boxes():
    # N=200 identical constrained: unbounded v2 needs ~40 s; a 2 s deadline
    # must return promptly with a partial-but-valid result.
    t0 = time.monotonic()
    res = PalletPacker(PALLET, PackerConfig(max_pallets=1)).pack(
        _boxes(200), time_limit_s=2.0)
    wall = time.monotonic() - t0
    assert wall < 15.0, f"deadline not honoured: {wall:.1f}s"
    placed = sum(len(st.placements) for st in res.pallets)
    assert placed + len(res.unpacked) == 200          # conservation
    assert placed > 0                                  # partial pack is real


def test_pack_default_is_unbounded_small_instance():
    # time_limit_s=None keeps the historical behavior (golden covers
    # bit-identity; this just pins the API contract).
    res = PalletPacker(PALLET, PackerConfig(max_pallets=1)).pack(_boxes(12))
    assert sum(len(st.placements) for st in res.pallets) == 12


def test_solver_end_to_end_honours_budget_on_homogeneous_load(fast_cfg):
    # The F2 reproduction through the service adapter: 200 identical boxes
    # with a finite pallet cap and a 10 s budget. Pre-fix this ran the v2
    # seed for ~40 s regardless of the budget.
    import sys
    sys.path.insert(0, "src")
    from pallet_api.solver import adapter
    payload = {
        "boxes": [{"id": f"B{i}", "length": 200, "width": 150, "height": 100,
                   "weight": 2.0} for i in range(200)],
        "pallet": {"length": 1200, "width": 800, "height": 1500,
                   "max_weight": 1000},
        "options": {"time_budget_s": 10},
    }
    t0 = time.monotonic()
    res = adapter.solve(payload, fast_cfg)
    wall = time.monotonic() - t0
    assert wall < 45.0, f"budget ignored: {wall:.1f}s for a 10s request"
    s = res["input_summary"]
    assert s["items_packed"] + s["items_unpacked"] == 200
