"""Schema (pydantic) tests — pure, run anywhere (no core, no Redis)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pallet_api.schemas import BoxIn, OptionsIn, PalletIn, PackRequest, JobState


def test_boxin_defaults():
    b = BoxIn(id="B1", length=300, width=200, height=150)
    assert b.weight == 0.0
    assert b.max_load_on_top is None        # None = unlimited
    assert b.rotations == "all"
    assert b.requires_full_support is False
    assert b.group is None


def test_boxin_rejects_unknown_rotation():
    with pytest.raises(ValidationError):
        BoxIn(id="B1", length=1, width=1, height=1, rotations="sideways")


def test_boxin_rejects_non_positive_dimension():
    with pytest.raises(ValidationError):
        BoxIn(id="B1", length=0, width=10, height=10)       # gt=0


def test_boxin_rejects_non_integer_dimension():
    with pytest.raises(ValidationError):
        BoxIn(id="B1", length=300.5, width=200, height=150)  # int field


def test_options_reject_out_of_range_values():
    with pytest.raises(ValidationError):
        OptionsIn(support_ratio=2.0)                         # le=1
    with pytest.raises(ValidationError):
        OptionsIn(max_pallets=0)                             # ge=1
    with pytest.raises(ValidationError):
        OptionsIn(seed=-5)                                   # ge=0 (F4: numpy
    # rejects negative seeds; must be a schema 422, not a solver_error)
    assert OptionsIn(seed=0).seed == 0                       # zero stays legal


def test_boxin_bounds_reject_abuse_vectors():
    # Hardening plan C1 (F5/F6/F12): well-typed but hostile values.
    box = dict(id="B1", length=1, width=1, height=1)
    with pytest.raises(ValidationError):
        BoxIn(**{**box, "id": "x" * 129})                  # id length cap
    with pytest.raises(ValidationError):
        BoxIn(**{**box, "id": ""})                         # empty id
    with pytest.raises(ValidationError):
        BoxIn(**box, group="g" * 129)                      # group length cap
    with pytest.raises(ValidationError):
        BoxIn(**box, weight=1e13)                          # above 1e12
    with pytest.raises(ValidationError):
        BoxIn(**box, weight=float("inf"))                  # F12: inf passed ge=0
    with pytest.raises(ValidationError):
        BoxIn(**box, max_load_on_top=float("inf"))
    b = BoxIn(**box, weight=1e12, group="g" * 128)         # at the bounds: legal
    assert b.weight == 1e12


def test_dims_are_bounded_at_1e6():
    # Hardening round 2 A3-1 (F16): unbounded dims silently overflow the
    # core's int64 hot-path products (areas, volumes).
    with pytest.raises(ValidationError):
        BoxIn(id="B1", length=1_000_001, width=1, height=1)
    b = BoxIn(id="B1", length=1_000_000, width=1_000_000, height=1_000_000)
    assert b.length == 1_000_000
    with pytest.raises(ValidationError):
        PalletIn(length=1_000_001, width=800, height=1500)
    with pytest.raises(ValidationError):
        BoxIn(id="B1", length=2**63, width=1, height=1)   # OverflowError class


def test_options_null_means_defaults():
    # A3-6: "options": null and omission are equivalent.
    req = PackRequest(
        boxes=[BoxIn(id="B1", length=300, width=200, height=150)],
        pallet=PalletIn(length=1200, width=1000, height=1500),
        options=None)
    assert req.options is not None
    assert req.options.max_pallets == 1


def test_palletin_bounds():
    p = dict(length=1200, width=800, height=1500)
    with pytest.raises(ValidationError):
        PalletIn(**p, max_weight=float("inf"))
    with pytest.raises(ValidationError):
        # F11: an overhang wider than the deck lets a box sit fully off it.
        PalletIn(**p, max_overhang=801)
    assert PalletIn(**p, max_overhang=800).max_overhang == 800


def test_options_bounds():
    with pytest.raises(ValidationError):
        OptionsIn(max_pallets=101)                         # C1 cap
    assert OptionsIn(max_pallets=100).max_pallets == 100
    with pytest.raises(ValidationError):
        OptionsIn(time_budget_s=float("inf"))


def test_packrequest_requires_at_least_one_box():
    with pytest.raises(ValidationError):
        PackRequest(boxes=[], pallet=PalletIn(length=1, width=1, height=1))


def test_options_defaults():
    o = OptionsIn()
    assert o.max_pallets == 1
    assert o.support_ratio == 0.8
    assert o.seed is None
    assert o.time_budget_s is None


def test_pallet_defaults():
    p = PalletIn(length=1, width=1, height=1)
    assert p.max_weight is None
    assert p.max_overhang == 0.0


def test_packrequest_roundtrip_fills_default_options():
    body = {
        "boxes": [{"id": "B1", "length": 300, "width": 200, "height": 150}],
        "pallet": {"length": 1200, "width": 1000, "height": 1500},
    }
    d = PackRequest(**body).model_dump()
    assert d["boxes"][0]["id"] == "B1"
    assert d["options"]["max_pallets"] == 1      # default-filled
    assert d["pallet"]["max_overhang"] == 0.0


def test_jobstate_rejects_unknown_status():
    with pytest.raises(ValidationError):
        JobState(job_id="x", status="weird")


# ------------------------------------------------------- F27: unknown fields
def test_unknown_option_field_rejected():
    # Round 3 (F27): a typo'd option used to be silently ignored — the solve
    # ran with defaults and the caller never knew ("suport_ratio": 0 packed
    # at 0.8). extra="forbid" turns it into a 422 naming the field.
    with pytest.raises(ValidationError, match="suport_ratio"):
        OptionsIn(suport_ratio=0.0)
    with pytest.raises(ValidationError, match="time_budget"):
        OptionsIn(time_budget=5)                 # missing the _s suffix


def test_unknown_box_and_pallet_fields_rejected():
    with pytest.raises(ValidationError, match="qty"):
        BoxIn(id="B1", length=1, width=1, height=1, qty=5)
    with pytest.raises(ValidationError, match="max_overhung"):
        PalletIn(length=100, width=100, height=100, max_overhung=10)


def test_unknown_toplevel_field_rejected():
    with pytest.raises(ValidationError, match="palet"):
        PackRequest(
            boxes=[{"id": "B1", "length": 1, "width": 1, "height": 1}],
            pallet={"length": 10, "width": 10, "height": 10},
            palet={"length": 10, "width": 10, "height": 10},
        )
