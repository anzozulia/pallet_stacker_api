"""Docker-tier tests for hardening round 2's load/floor model (F17 + F19):
the transitive load bearing flag and the floor deck-contact rule under
overhang. See docs/06_hardening_plan.md round-2 addendum + upstream report 36.
"""
from __future__ import annotations

import pytest

pp = pytest.importorskip("pallet_packer")

from pallet_packer import Box, Pallet, PackerConfig            # noqa: E402
from pallet_packer.brkga_v3_5 import brkga_pack_v35            # noqa: E402
from pallet_packer.validate import validate                    # noqa: E402
from pallet_packer.packer import PackResult                    # noqa: E402


def _solve(boxes, pallet, cfg, budget=4.0):
    return brkga_pack_v35(boxes, pallet, cfg, time_limit_s=budget,
                          population_size=20, n_populations=1, patience=5,
                          seed=42, max_pallets=1)


def _transitive_max_ratio(res):
    """Max (transitive load / mlot) over placed boxes, from pure geometry."""
    worst = 0.0
    for st in res.pallets:
        ps = sorted(st.placements, key=lambda p: -p.z)
        load = {id(p): 0.0 for p in ps}
        for p in ps:
            sups = []
            for q in ps:
                if q is p or abs(q.z2 - p.z) > 1e-6:
                    continue
                ox = min(p.x2, q.x2) - max(p.x, q.x)
                oy = min(p.y2, q.y2) - max(p.y, q.y)
                if ox > 0 and oy > 0:
                    sups.append((q, ox * oy))
            ta = sum(a for _, a in sups)
            if ta <= 0:
                continue
            outflow = p.box.weight + load[id(p)]
            for q, a in sups:
                load[id(q)] += outflow * (a / ta)
        for p in ps:
            m = p.box.max_load_on_top
            if m and m < 1e17:
                worst = max(worst, load[id(p)] / m)
    return worst


def _ten_stack_instance():
    boxes = [Box(id=f"B{i}", length=400, width=400, height=140, weight=10.0,
                 max_load_on_top=10.5) for i in range(10)]
    return boxes, Pallet(length=400, width=400, height=1500)


def test_direct_model_overloads_and_transitive_model_does_not():
    # F19: every link in a pure column is individually legal (10 <= 10.5)
    # but the bottom box transitively carries a multiple of its limit. The
    # flag must make the engines enforce the physical bound.
    boxes, pallet = _ten_stack_instance()
    res_off = _solve(boxes, pallet, PackerConfig(
        max_pallets=1, enforce_load_bearing=True))
    res_on = _solve(boxes, pallet, PackerConfig(
        max_pallets=1, enforce_load_bearing=True,
        transitive_load_bearing=True))
    n_off = sum(len(st.placements) for st in res_off.pallets)
    n_on = sum(len(st.placements) for st in res_on.pallets)
    assert n_off == 10                                  # historical behavior
    assert _transitive_max_ratio(res_off) > 1.5         # ...which crushes
    assert n_on < n_off                                 # constraint bites
    assert _transitive_max_ratio(res_on) <= 1.0 + 1e-6  # nothing crushed
    # conservation
    assert n_on + len(res_on.unpacked) == 10


def test_validate_transitive_flag_catches_crushed_column():
    # The safety net must agree with the engine's model on both settings.
    boxes, pallet = _ten_stack_instance()
    res_off = _solve(boxes, pallet, PackerConfig(
        max_pallets=1, enforce_load_bearing=True))
    direct_cfg = PackerConfig(max_pallets=1, enforce_load_bearing=True)
    trans_cfg = PackerConfig(max_pallets=1, enforce_load_bearing=True,
                             transitive_load_bearing=True)
    assert not validate(res_off, pallet, direct_cfg)     # direct-legal
    errs = validate(res_off, pallet, trans_cfg)
    assert any("max_load_on_top" in e for e in errs)     # transitively crushed


def test_overhang_floor_boxes_keep_deck_contact():
    # F17: with overhang active a floor box must still rest ON the deck
    # (deck contact >= support_ratio) — no more boxes floating in mid-air
    # beside the pallet.
    boxes = [Box(id=f"O{i}", length=400, width=400, height=200, weight=4.0)
             for i in range(12)]
    pallet = Pallet(length=1200, width=800, height=1500, max_overhang=800)
    cfg = PackerConfig(max_pallets=1, allow_pallet_overhang=True,
                       support_ratio=0.8, enforce_load_bearing=True)
    res = _solve(boxes, pallet, cfg, budget=5.0)
    for st in res.pallets:
        for p in st.placements:
            if p.z <= 1e-6:
                cx = min(p.x2, 1200.0) - max(p.x, 0.0)
                cy = min(p.y2, 800.0) - max(p.y, 0.0)
                contact = cx * cy if (cx > 0 and cy > 0) else 0.0
                assert contact / (p.dx * p.dy) >= 0.8 - 1e-6, \
                    f"{p.box.id} floats beside the deck at ({p.x},{p.y})"
    assert not validate(res, pallet, cfg)


def test_validate_flags_offdeck_floor_box_under_overhang():
    # The validator itself must reject a hand-built off-deck floor box.
    from pallet_packer.models import Placement, Rotation
    from pallet_packer.packer import PalletState
    pallet = Pallet(length=1200, width=800, height=1500, max_overhang=800)
    cfg = PackerConfig(allow_pallet_overhang=True, support_ratio=0.8)
    b = Box(id="X", length=400, width=400, height=200, weight=4.0)
    st = PalletState(pallet, "P001", cfg)
    st.placements.append(Placement(box=b, rotation=Rotation.LWH,
                                   x=1200.0, y=0.0, z=0.0))  # fully off deck
    errs = validate(PackResult(pallets=[st], unpacked=[]), pallet, cfg)
    assert any("deck" in e for e in errs)


def test_exact_fit_tiling_packs_all_via_service(fast_cfg):
    # F18 mitigation: the v2 seed (forced for small instances by the
    # adapter) tiles the 2x2x2 exact fit the greedy rotation argmax broke
    # (7/8 deterministically before round 2).
    import sys
    sys.path.insert(0, "src")
    from pallet_api.solver import adapter
    payload = {
        "boxes": [{"id": f"E{i}", "length": 600, "width": 400, "height": 750,
                   "weight": 10.0} for i in range(8)],
        "pallet": {"length": 1200, "width": 800, "height": 1500},
        "options": {"time_budget_s": 10},
    }
    res = adapter.solve(payload, fast_cfg)
    assert res["input_summary"]["items_packed"] == 8
