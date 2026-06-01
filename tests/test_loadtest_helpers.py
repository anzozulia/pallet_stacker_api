"""Tests for the load-test harness pure helpers (scripts/loadtest.py).
Host-safe — the harness is stdlib-only. Protects the Phase 6 measurement code."""
from __future__ import annotations

import loadtest as L


def test_pct_basic():
    assert L._pct([1, 2, 3, 4, 5], 0.5) == 3
    assert L._pct([1, 2, 3, 4, 5], 1.0) == 5
    assert L._pct([], 0.5) is None
    assert L._pct([None, 1, None], 0.5) == 1     # None values dropped


def test_max_overlap_disjoint_is_one():
    recs = [{"t_first_running": 0, "t_done": 1},
            {"t_first_running": 2, "t_done": 3}]
    assert L._max_overlap(recs) == 1


def test_max_overlap_nested_is_two():
    recs = [{"t_first_running": 0, "t_done": 5},
            {"t_first_running": 1, "t_done": 2}]
    assert L._max_overlap(recs) == 2


def test_max_overlap_ignores_incomplete():
    recs = [{"t_first_running": None, "t_done": 5},
            {"t_first_running": 1, "t_done": 2}]
    assert L._max_overlap(recs) == 1


def test_make_request_shape():
    r = L.make_request(7, seed=1, budget=30)
    assert len(r["boxes"]) == 7
    ids = [b["id"] for b in r["boxes"]]
    assert len(set(ids)) == 7                    # unique ids
    for b in r["boxes"]:
        for k in ("length", "width", "height"):
            assert isinstance(b[k], int) and b[k] > 0   # positive integer dims
    assert r["options"]["time_budget_s"] == 30
    assert r["pallet"]["length"] == 1200


def test_summarize_basic():
    recs = [{"status": "done", "solve_s": 2.0, "e2e_s": 3.0,
             "queue_wait_s": 1.0, "t_first_running": 0, "t_done": 2}]
    s = L.summarize(recs, wall_s=2.0, label="x", cfg={})
    assert s["n_done"] == 1
    assert s["throughput_jobs_per_min"] == 30.0   # 1 job / 2s * 60
    assert s["statuses"] == {"done": 1}
