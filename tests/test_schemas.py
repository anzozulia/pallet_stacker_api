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
