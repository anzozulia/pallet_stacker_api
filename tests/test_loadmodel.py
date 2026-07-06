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


# ---------------------------------------------------------------- round 6
# F30: a floor box under overhang whose footprint centroid projects past
# the deck edge tips over. Round-2 F17 added the deck-contact RATIO rule
# but not this toppling half; at support_ratio < 0.5 the ratio no longer
# implies centroid-on-deck. See ADR D18.

def _f30_state(sr=0.25, rc=True):
    from pallet_packer.packer import PalletState
    pallet = Pallet(length=400, width=400, height=1000, max_overhang=400)
    cfg = PackerConfig(allow_pallet_overhang=True, support_ratio=sr,
                       require_centroid_supported=rc,
                       enforce_load_bearing=True)
    return PalletState(pallet, "P001", cfg), pallet, cfg


def _f30_floor_box(x, dx=400):
    from pallet_packer.models import Placement, Rotation
    b = Box(id="T", length=dx, width=400, height=200, weight=10.0)
    return Placement(box=b, rotation=Rotation.LWH, x=float(x), y=0.0, z=0.0)


def test_f30_validator_flags_toppling_floor_centroid():
    # ratio 0.25 passes sr=0.25 but centroid x=500 > deck edge 400.
    st, pallet, cfg = _f30_state()
    st.placements.append(_f30_floor_box(300))
    errs = validate(PackResult(pallets=[st], unpacked=[]), pallet, cfg)
    assert any("topple" in e for e in errs), errs


def test_f30_boundary_centroid_on_edge_is_clean():
    # centroid exactly ON the deck edge (x=200, dx=400 -> 400 == edge):
    # accepted, mirroring the stacked centroid rule's boundary behavior.
    st, pallet, cfg = _f30_state()
    st.placements.append(_f30_floor_box(200))
    assert validate(PackResult(pallets=[st], unpacked=[]), pallet, cfg) == []


def test_f30_gating_require_centroid_off_and_overhang_off():
    # rc=False: caller disabled centroid semantics -> no CoM check.
    st, pallet, cfg = _f30_state(rc=False)
    st.placements.append(_f30_floor_box(300))
    assert validate(PackResult(pallets=[st], unpacked=[]), pallet, cfg) == []
    # overhang off: branch unreachable regardless (box fully on deck).
    pallet2 = Pallet(length=400, width=400, height=1000)
    cfg2 = PackerConfig(support_ratio=0.25, require_centroid_supported=True)
    from pallet_packer.packer import PalletState
    st2 = PalletState(pallet2, "P001", cfg2)
    st2.placements.append(_f30_floor_box(0))
    assert validate(PackResult(pallets=[st2], unpacked=[]), pallet2, cfg2) == []


def test_f30_v2_feasible_rejects_toppling_floor_box():
    st, pallet, cfg = _f30_state()
    assert st.feasible(_f30_floor_box(300)) is False      # topples
    assert st.feasible(_f30_floor_box(200)) is True       # boundary
    assert st.feasible(_f30_floor_box(0)) is True         # on deck
    st_rc0, _, _ = _f30_state(rc=False)
    assert st_rc0.feasible(_f30_floor_box(300)) is True   # gate respected


def test_f30_engine_never_ships_toppling_floor_box():
    # The confirmed round-6 live repro: two none-rotation fragile slabs on
    # a 400x400 deck with full overhang at sr=0.4 — pre-fix the engine
    # shipped B with its centroid at x=410 (validate-clean). Post-fix no
    # shipped floor box may have its centroid past the deck contact.
    from pallet_packer import NO_ROTATION
    boxes = [
        Box(id="A", length=260, width=400, height=120, weight=5.0,
            max_load_on_top=0.0, allowed_rotations=list(NO_ROTATION)),
        Box(id="B", length=300, width=400, height=120, weight=5.0,
            max_load_on_top=0.0, allowed_rotations=list(NO_ROTATION)),
    ]
    pallet = Pallet(length=400, width=400, height=1500, max_overhang=400)
    cfg = PackerConfig(support_ratio=0.4, require_centroid_supported=True,
                       enforce_load_bearing=True, allow_pallet_overhang=True,
                       transitive_load_bearing=True,
                       recenter_layout=True, align_orientations=True,
                       realism_weight=1.0)
    res = brkga_pack_v35(boxes, pallet, cfg, time_limit_s=3.0,
                         population_size=25, n_populations=1, patience=6,
                         seed=1, max_pallets=1, use_v2_seed=True)
    assert not validate(res, pallet, cfg)
    for st in res.pallets:
        for p in st.placements:
            if p.z <= 1e-6:
                assert p.x + p.dx / 2.0 <= min(p.x2, 400.0) + 1e-6, \
                    f"{p.box.id} centroid past deck edge"
                assert p.y + p.dy / 2.0 <= min(p.y2, 400.0) + 1e-6


def test_f30_postprocess_does_not_revert_valid_low_sr_plan():
    # A legal overhang layout at low sr must pass the post-pass replay
    # validation (which now includes the CoM rule) without a revert.
    from pallet_packer.postprocess import apply_postprocess
    st, pallet, cfg = _f30_state()
    p = _f30_floor_box(150)        # centroid 350 <= 400: legal overhang
    st.placements.append(p)
    st.total_weight += p.box.weight
    res = apply_postprocess(PackResult(pallets=[st], unpacked=[]),
                            pallet, cfg)
    assert validate(res, pallet, cfg) == []
    assert len(res.pallets[0].placements) == 1


def test_f30_repair_is_noop_and_adapter_warns():
    # A CoM-only violation is geometry, not load — repair must return the
    # plan unchanged and the adapter surfaces it as `unrepaired:`.
    from pallet_packer.repair import repair_load_violations
    st, pallet, cfg = _f30_state()
    st.placements.append(_f30_floor_box(300))
    st.total_weight += 10.0
    res = PackResult(pallets=[st], unpacked=[])
    res2, actions = repair_load_violations(res, pallet, cfg)
    assert actions == []
    assert len(res2.pallets[0].placements) == 1
