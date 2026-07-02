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


def test_group_blank_is_normalised_to_none():
    # C3/F10: "" is falsy-but-not-None; passing it through would co-locate
    # every empty-group box onto ONE pallet as a real group.
    base = {"id": "B1", "length": 10, "width": 10, "height": 10}
    assert adapter._box({**base, "group": ""}).group is None
    assert adapter._box({**base, "group": "   "}).group is None
    assert adapter._box({**base, "group": "CUST1"}).group == "CUST1"


def test_core_gate_rejects_weight_near_no_limit_sentinel():
    # C4/F5: weights >= 1e18 alias the decoders' _NO_LIMIT sentinel and become
    # silently unpackable; the gate rejects >= 1e15 (500 x 1e15 stays < 1e18).
    body = {"boxes": [{"id": "B1", "length": 10, "width": 10, "height": 10,
                       "weight": 1e15}],
            "pallet": {"length": 100, "width": 100, "height": 100}}
    with pytest.raises(PackingInputError, match="supported range"):
        adapter.validate_request(body, max_boxes=500)
    body["boxes"][0]["weight"] = 1e14                # under the bound: fine
    adapter.validate_request(body, max_boxes=500)


def test_core_gate_rejects_dims_over_1e6():
    # A3-1 (F16): the gate covers library callers too — dims past 1e6 hit
    # int64 wraparound territory in the JIT hot paths; 2**63 raised a raw
    # OverflowError as a solver_error before this guard.
    body = {"boxes": [{"id": "B1", "length": 1_000_001, "width": 10,
                       "height": 10}],
            "pallet": {"length": 100, "width": 100, "height": 100}}
    with pytest.raises(PackingInputError, match="supported range"):
        adapter.validate_request(body, max_boxes=500)
    body["boxes"][0]["length"] = 2**63
    with pytest.raises(PackingInputError, match="supported range"):
        adapter.validate_request(body, max_boxes=500)
    body["boxes"][0]["length"] = 1_000_000
    body["pallet"] = {"length": 1_000_000, "width": 1_000_000,
                      "height": 1_000_000}
    adapter.validate_request(body, max_boxes=500)      # at the bound: fine


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
