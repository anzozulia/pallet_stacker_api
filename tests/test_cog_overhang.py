"""Round-7 (F35 / ADR D20): sub-assembly toppling under overhang.

A box stacked on the overhanging part of a floor box passes every per-box
check (F30 floor centroid, stacked centroid-over-supporter, support ratio,
load) yet the combined {floor + stacked} weighted CoG can project past the
deck edge — the pair tips off the deck as a unit, and pre-fix validate()
returned clean. The fix auto-activates the deck-footprint CoG envelope
([0,L]x[0,W]) under overhang: the engine's running-CoG reject avoids it
during decode and validate() enforces it as the safety net.
"""
from __future__ import annotations

import math

import pytest

pp = pytest.importorskip("pallet_packer")

from pallet_packer import Box, Pallet, PackerConfig                # noqa: E402
from pallet_packer.models import Placement, Rotation               # noqa: E402
from pallet_packer.packer import PalletState, PackResult           # noqa: E402
from pallet_packer.validate import validate                        # noqa: E402

ROT = Rotation.LWH


def _cfg(overhang=True):
    # Mirrors adapter._config under/without overhang (F35).
    return PackerConfig(
        support_ratio=0.8, require_centroid_supported=True,
        enforce_load_bearing=True, allow_pallet_overhang=overhang,
        transitive_load_bearing=True,
        cog_envelope_fraction=0.5 if overhang else 1.0,
        cog_check_min_load_fraction=0.0 if overhang else 1.0)


def _F():
    return Box(id="F", length=1000, width=1000, height=100, weight=1.0,
               max_load_on_top=100.0)


def _S():
    return Box(id="S", length=200, width=1000, height=100, weight=10.0)


def test_f35_hand_case_flagged_by_validate():
    pal = Pallet(length=1000, width=1000, height=1500, max_weight=math.inf,
                 max_overhang=1000)
    cfg = _cfg()
    st = PalletState(pal, "P001", cfg)
    st.placements += [Placement(box=_F(), rotation=ROT, x=200, y=0, z=0),
                      Placement(box=_S(), rotation=ROT, x=1000, y=0, z=100)]
    st.total_weight = 11.0
    errs = validate(PackResult(pallets=[st], unpacked=[]), pal, cfg)
    assert any("CoG" in e for e in errs), errs   # combined CoG x=1063.6 > 1000


def test_f35_engine_feasible_rejects_the_tipping_stack():
    pal = Pallet(length=1000, width=1000, height=1500, max_weight=math.inf,
                 max_overhang=1000)
    cfg = _cfg()
    st = PalletState(pal, "P001", cfg)
    st._commit(Placement(box=_F(), rotation=ROT, x=200, y=0, z=0))
    assert st.feasible(Placement(box=_S(), rotation=ROT, x=1000, y=0, z=100)) \
        is False


def test_f35_balanced_overhang_stays_clean():
    pal = Pallet(length=1000, width=1000, height=1500, max_weight=math.inf,
                 max_overhang=1000)
    cfg = _cfg()
    st = PalletState(pal, "P001", cfg)
    b1 = Box(id="B1", length=600, width=1000, height=100, weight=5.0)
    b2 = Box(id="B2", length=600, width=1000, height=100, weight=5.0)
    st.placements += [Placement(box=b1, rotation=ROT, x=0, y=0, z=0),
                      Placement(box=b2, rotation=ROT, x=400, y=0, z=100)]
    st.total_weight = 10.0
    assert not [e for e in validate(PackResult(pallets=[st], unpacked=[]),
                                    pal, cfg) if "CoG" in e]


def test_f35_overhang_off_cog_inert():
    # Without overhang the CoG check must not fire at all (bit-identical to
    # the historical behaviour — frac 1.0, cog disabled).
    pal = Pallet(length=1000, width=1000, height=1500, max_weight=math.inf)
    cfg = _cfg(overhang=False)
    st = PalletState(pal, "P001", cfg)
    st.placements.append(Placement(box=_F(), rotation=ROT, x=0, y=0, z=0))
    st.total_weight = 1.0
    assert not [e for e in validate(PackResult(pallets=[st], unpacked=[]),
                                    pal, cfg) if "CoG" in e]


def test_f35_end_to_end_no_tipping_shipped(fast_cfg):
    # Through the service adapter: an overhang request whose dense packing
    # would tip must ship balanced-or-dropped, validate-clean, with no CoG
    # warning.
    import sys
    sys.path.insert(0, "src")
    from pallet_api.solver import adapter
    payload = {
        "boxes": ([{"id": "F", "length": 1000, "width": 1000, "height": 100,
                    "weight": 1, "max_load_on_top": 100}]
                  + [{"id": f"H{i}", "length": 200, "width": 500, "height": 100,
                      "weight": 12} for i in range(6)]),
        "pallet": {"length": 1000, "width": 1000, "height": 1500,
                   "max_overhang": 1000},
        "options": {"support_ratio": 0.8, "time_budget_s": 3},
    }
    res = adapter.solve(payload, fast_cfg)
    L = 1000
    for pal in res["pallets"]:
        tw = sum(it["weight"] for it in pal["items"])
        if tw <= 0:
            continue
        cx = sum(it["weight"] * (it["position"]["x"] + it["dimensions"]["L"] / 2)
                 for it in pal["items"]) / tw
        cy = sum(it["weight"] * (it["position"]["y"] + it["dimensions"]["W"] / 2)
                 for it in pal["items"]) / tw
        assert -1e-6 <= cx <= L + 1e-6, f"pallet CoG x={cx} off deck"
        assert -1e-6 <= cy <= L + 1e-6, f"pallet CoG y={cy} off deck"
    assert not any("CoG" in w for w in res.get("warnings", []))
