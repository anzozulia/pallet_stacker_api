"""Solver-adapter tests — the Phase 1 acceptance (request -> gate -> solve ->
to_json). Needs the core; skipped on a host without it."""
from __future__ import annotations

import math

import pytest

pytest.importorskip("pallet_packer")

from pallet_packer import PackingInputError, THIS_SIDE_UP  # noqa: E402
from pallet_api.solver import adapter                      # noqa: E402


def test_validate_request_accepts_good_instance(pack_payload):
    adapter.validate_request(pack_payload(3), max_boxes=500)   # must not raise


def test_validate_request_rejects_float_dims(pack_payload):
    with pytest.raises(PackingInputError):
        adapter.validate_request(pack_payload(2, bad=True), max_boxes=500)


def test_validate_request_rejects_over_cap(pack_payload):
    with pytest.raises(PackingInputError):
        adapter.validate_request(pack_payload(3), max_boxes=2)


def test_validate_request_rejects_duplicate_ids():
    body = {
        "boxes": [{"id": "X", "length": 10, "width": 10, "height": 10},
                  {"id": "X", "length": 10, "width": 10, "height": 10}],
        "pallet": {"length": 100, "width": 100, "height": 100},
    }
    with pytest.raises(PackingInputError):
        adapter.validate_request(body, max_boxes=500)


def test_build_inputs_field_mapping():
    body = {
        "boxes": [
            {"id": "B1", "length": 10, "width": 10, "height": 10,
             "max_load_on_top": None, "rotations": "this_side_up", "group": "G1"},
            {"id": "B2", "length": 10, "width": 10, "height": 10,
             "max_load_on_top": 0},
        ],
        "pallet": {"length": 100, "width": 100, "height": 100, "max_weight": None},
    }
    boxes, pallet = adapter.build_inputs(body)
    assert math.isinf(boxes[0].max_load_on_top)        # None -> unlimited
    assert boxes[1].max_load_on_top == 0.0             # 0 -> fragile
    assert list(boxes[0].allowed_rotations) == list(THIS_SIDE_UP)
    assert boxes[0].group == "G1"
    assert math.isinf(pallet.max_weight)               # None -> unlimited


def test_config_overhang_flag():
    p = {"length": 100, "width": 100, "height": 100}
    c0 = adapter._config({"pallet": {**p, "max_overhang": 0}, "options": {}})
    assert c0.allow_pallet_overhang is False
    c1 = adapter._config({"pallet": {**p, "max_overhang": 5}, "options": {}})
    assert c1.allow_pallet_overhang is True


def test_solve_known_instance_returns_valid_result(fast_cfg, pack_payload):
    res = adapter.solve(pack_payload(5, budget=5), fast_cfg)
    assert isinstance(res, dict)
    assert "input_summary" in res and "pallets" in res
    s = res["input_summary"]
    # conservation: every box is either packed or explicitly unpacked
    assert s["items_packed"] + s["items_unpacked"] == 5


def test_solve_result_is_strict_json_safe_with_unlimited_caps(fast_cfg):
    # No max_weight / max_load_on_top -> unlimited -> inf internally. The result
    # must still serialise under allow_nan=False (what FastAPI/starlette use), or
    # the GET /jobs result fetch 500s. Regression for the inf-in-result bug.
    import json
    body = {
        "boxes": [{"id": "B1", "length": 300, "width": 200, "height": 150}],
        "pallet": {"length": 1200, "width": 1000, "height": 1500},
        "options": {"time_budget_s": 5},
    }
    res = adapter.solve(body, fast_cfg)
    json.dumps(res, allow_nan=False)        # must not raise


def test_solve_is_deterministic_at_fixed_seed(fast_cfg, pack_payload):
    a = adapter.solve(pack_payload(5, budget=5, seed=7), fast_cfg)
    b = adapter.solve(pack_payload(5, budget=5, seed=7), fast_cfg)
    assert a["input_summary"]["items_packed"] == b["input_summary"]["items_packed"]
