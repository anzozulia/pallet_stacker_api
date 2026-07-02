"""Docker-tier tests for the realism post-passes (core/pallet_packer/postprocess.py).

Covers: recenter targeting/clamping/idempotence, orientation re-alignment
(dominance, dependents gate, revert-on-violation), and that the
brkga_pack_v35 wrapper applies the passes on the restart and group paths.
"""
from __future__ import annotations

import pytest

pp = pytest.importorskip("pallet_packer")

from pallet_packer import Box, Pallet, PackerConfig            # noqa: E402
from pallet_packer.models import Placement, Rotation           # noqa: E402
from pallet_packer.packer import PackResult, PalletState       # noqa: E402
from pallet_packer.postprocess import (                        # noqa: E402
    align_orientations_pass,
    apply_postprocess,
    recenter_pass,
)
from pallet_packer.validate import validate                    # noqa: E402
from pallet_packer.brkga_v3_5 import brkga_pack_v35            # noqa: E402


def _cfg(**kw) -> PackerConfig:
    base = dict(recenter_layout=True, align_orientations=True,
                cog_envelope_fraction=1.0, cog_check_min_load_fraction=1.0)
    base.update(kw)
    return PackerConfig(**base)


def _state(pallet, cfg, spec):
    """spec: [(box, rotation, x, y, z), ...] -> a hand-built PalletState."""
    st = PalletState(pallet, "P001", cfg)
    for box, rot, x, y, z in spec:
        st.placements.append(Placement(box=box, rotation=rot, x=x, y=y, z=z))
        st.total_weight += box.weight
    return st


PALLET = Pallet(length=1200, width=800, height=1500, max_weight=float("inf"))


# ---------------------------------------------------------------------------
# recenter_pass
# ---------------------------------------------------------------------------
def test_recenter_single_box_centered():
    cfg = _cfg()
    b = Box(id="A", length=600, width=400, height=400, weight=25.0)
    st = _state(PALLET, cfg, [(b, Rotation.LWH, 0.0, 0.0, 0.0)])
    recenter_pass(st, PALLET, cfg)
    p = st.placements[0]
    assert (p.x, p.y, p.z) == (300.0, 200.0, 0.0)     # (L-600)//2, (W-400)//2
    assert p.x == int(p.x) and p.y == int(p.y)         # integer grid preserved
    assert not validate(PackResult(pallets=[st], unpacked=[]), PALLET, cfg)


def test_recenter_weighted_cog_targets_deck_center():
    cfg = _cfg()
    heavy = Box(id="H", length=400, width=400, height=400, weight=100.0)
    light = Box(id="L", length=400, width=400, height=400, weight=1.0)
    st = _state(PALLET, cfg, [(heavy, Rotation.LWH, 0.0, 0.0, 0.0),
                              (light, Rotation.LWH, 400.0, 0.0, 0.0)])
    recenter_pass(st, PALLET, cfg)
    # Weighted CoG x = (200*100 + 600*1)/101 ~ 203.96 from bbox left;
    # target x_min = floor(600 - 203.96) = 396 (clamped to [0, 400]).
    assert min(p.x for p in st.placements) == 396.0
    cog_x = (sum(p.box.weight * (p.x + p.dx / 2) for p in st.placements)
             / sum(p.box.weight for p in st.placements))
    assert abs(cog_x - 600.0) < 1.0                    # centred within the grid


def test_recenter_skipped_when_caller_owns_cog_envelope():
    # C5/F7: an explicit (possibly off-centre) CoG range is the caller's
    # placement decision; recentring to the deck centre could violate it and
    # the fractional-envelope argument does not apply to explicit ranges.
    cfg = _cfg()
    pallet = Pallet(length=1200, width=800, height=1500,
                    max_weight=float("inf"), cog_x_range=(0.0, 300.0))
    b = Box(id="A", length=600, width=400, height=400, weight=25.0)
    st = _state(pallet, cfg, [(b, Rotation.LWH, 0.0, 0.0, 0.0)])
    assert recenter_pass(st, pallet, cfg) == (0.0, 0.0)
    assert (st.placements[0].x, st.placements[0].y) == (0.0, 0.0)


def test_apply_postprocess_global_budget_skips_align_keeps_recenter():
    # C6/F8: with the shared budget spent, align is skipped (the yaw mix
    # stays) but the O(N) recenter still runs.
    cfg = _cfg()
    a = Box(id="A", length=300, width=200, height=100, weight=5.0)
    b = Box(id="B", length=300, width=200, height=100, weight=5.0)
    st = _state(PALLET, cfg, [(a, Rotation.LWH, 0.0, 0.0, 0.0),
                              (b, Rotation.WLH, 300.0, 0.0, 0.0)])
    res = apply_postprocess(PackResult(pallets=[st], unpacked=[]),
                            PALLET, cfg, time_budget_s=0.0)
    ps = res.pallets[0].placements
    assert len({(round(p.dims[0]), round(p.dims[1])) for p in ps}) == 2
    assert min(p.x for p in ps) > 0.0                  # recentred anyway


def test_recenter_idempotent_and_z_untouched():
    cfg = _cfg()
    b = Box(id="A", length=300, width=300, height=300, weight=5.0)
    c = Box(id="B", length=300, width=300, height=300, weight=5.0)
    st = _state(PALLET, cfg, [(b, Rotation.LWH, 0.0, 0.0, 0.0),
                              (c, Rotation.LWH, 0.0, 0.0, 300.0)])
    recenter_pass(st, PALLET, cfg)
    once = [(p.x, p.y, p.z) for p in st.placements]
    tx, ty = recenter_pass(st, PALLET, cfg)
    assert (tx, ty) == (0.0, 0.0)
    assert [(p.x, p.y, p.z) for p in st.placements] == once
    assert {p.z for p in st.placements} == {0.0, 300.0}


def test_recenter_never_creates_overhang():
    # Layout wider than the deck (legal via overhang) must stay flush at 0.
    cfg = _cfg(allow_pallet_overhang=True)
    pallet = Pallet(length=1200, width=800, height=1500,
                    max_weight=float("inf"), max_overhang=50)
    b1 = Box(id="A", length=650, width=450, height=400, weight=20.0)
    b2 = Box(id="B", length=600, width=450, height=400, weight=20.0)
    st = _state(pallet, cfg, [(b1, Rotation.LWH, 0.0, 0.0, 0.0),
                              (b2, Rotation.LWH, 650.0, 0.0, 0.0)])   # span 1250
    recenter_pass(st, pallet, cfg)
    assert min(p.x for p in st.placements) == 0.0
    assert not validate(PackResult(pallets=[st], unpacked=[]), pallet, cfg)


# ---------------------------------------------------------------------------
# align_orientations_pass
# ---------------------------------------------------------------------------
def _mk_sku(i):
    return Box(id=f"S{i}", length=400, width=300, height=200, weight=8.0)


def test_align_deviant_joins_dominant_orientation():
    cfg = _cfg()
    a, b, c = _mk_sku(1), _mk_sku(2), _mk_sku(3)
    st = _state(PALLET, cfg, [
        (a, Rotation.LWH, 0.0, 0.0, 0.0),       # 400x300 footprint
        (b, Rotation.LWH, 400.0, 0.0, 0.0),
        (c, Rotation.WLH, 800.0, 0.0, 0.0),     # deviant: 300x400 footprint
    ])
    swaps = align_orientations_pass(st, PALLET, cfg)
    assert swaps == 1
    assert all((p.dx, p.dy, p.dz) == (400.0, 300.0, 200.0)
               for p in st.placements)
    assert not validate(PackResult(pallets=[st], unpacked=[]), PALLET, cfg)


def test_align_dependents_gate_blocks_dz_change():
    # Dominant orientation changes dz; the deviant supports another box, so
    # the swap must be skipped entirely.
    cfg = _cfg()
    m1, m2, dev = _mk_sku(1), _mk_sku(2), _mk_sku(3)
    top = Box(id="T", length=200, width=200, height=100, weight=1.0)
    st = _state(PALLET, cfg, [
        (m1, Rotation.LHW, 0.0, 0.0, 0.0),      # 400x200x300
        (m2, Rotation.LHW, 400.0, 0.0, 0.0),
        (dev, Rotation.LWH, 800.0, 0.0, 0.0),   # 400x300x200, carries `top`
        (top, Rotation.LWH, 850.0, 25.0, 200.0),
    ])
    align_orientations_pass(st, PALLET, cfg)
    p_dev = next(p for p in st.placements if p.box.id == "S3")
    assert p_dev.rotation == Rotation.LWH        # unchanged: dz would flip


def test_align_reverts_swap_that_overlaps_neighbor():
    # The yaw swap to dominant would grow the deviant into its neighbour.
    cfg = _cfg()
    m1, m2, dev = _mk_sku(1), _mk_sku(2), _mk_sku(3)
    st = _state(PALLET, cfg, [
        (dev, Rotation.WLH, 0.0, 0.0, 0.0),     # 300x400 footprint at x 0-300
        (m1, Rotation.LWH, 310.0, 0.0, 0.0),    # x 310-710: swap would overlap
        (m2, Rotation.LWH, 710.0, 0.0, 0.0),
    ])
    align_orientations_pass(st, PALLET, cfg)
    p_dev = next(p for p in st.placements if p.box.id == "S3")
    assert p_dev.rotation == Rotation.WLH        # applied-then-reverted
    assert not validate(PackResult(pallets=[st], unpacked=[]), PALLET, cfg)


def test_apply_postprocess_idempotent():
    cfg = _cfg()
    a, b, c = _mk_sku(1), _mk_sku(2), _mk_sku(3)
    st = _state(PALLET, cfg, [
        (a, Rotation.LWH, 0.0, 0.0, 0.0),
        (b, Rotation.LWH, 400.0, 0.0, 0.0),
        (c, Rotation.WLH, 800.0, 0.0, 0.0),
    ])
    res = PackResult(pallets=[st], unpacked=[])
    apply_postprocess(res, PALLET, cfg)
    once = [(p.box.id, p.rotation, p.x, p.y, p.z) for p in st.placements]
    apply_postprocess(res, PALLET, cfg)
    assert [(p.box.id, p.rotation, p.x, p.y, p.z)
            for p in st.placements] == once


def test_apply_postprocess_flags_off_is_noop():
    cfg = _cfg(recenter_layout=False, align_orientations=False)
    b = Box(id="A", length=600, width=400, height=400, weight=25.0)
    st = _state(PALLET, cfg, [(b, Rotation.LWH, 0.0, 0.0, 0.0)])
    apply_postprocess(PackResult(pallets=[st], unpacked=[]), PALLET, cfg)
    assert (st.placements[0].x, st.placements[0].y) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# wrapper integration: restart + group paths
# ---------------------------------------------------------------------------
_FAST = dict(time_limit_s=6.0, population_size=30, n_populations=2,
             patience=8, n_modes=6, verbose=False)


def _assert_recentred(st, pallet):
    """A recentred layout has its slack split, not piled on the far side —
    on every axis where the layout doesn't span the full deck."""
    x_min = min(p.x for p in st.placements)
    x_max = max(p.x2 for p in st.placements)
    y_min = min(p.y for p in st.placements)
    y_max = max(p.y2 for p in st.placements)
    axes_with_slack = 0
    if x_max - x_min < pallet.length:
        assert x_min > 0.0, (x_min, x_max)
        axes_with_slack += 1
    if y_max - y_min < pallet.width:
        assert y_min > 0.0, (y_min, y_max)
        axes_with_slack += 1
    assert axes_with_slack > 0                # can't span both dims fully here


def test_solver_wrapper_recentres_underfilled_load():
    cfg = _cfg(realism_weight=1.0)
    boxes = [Box(id=f"C{i}", length=300, width=300, height=300, weight=10.0)
             for i in range(6)]
    res = brkga_pack_v35(boxes, PALLET, cfg, seed=42, **_FAST)
    assert not res.unpacked
    _assert_recentred(res.pallets[0], PALLET)  # no longer corner-jammed
    assert not validate(res, PALLET, cfg)


def test_solver_wrapper_restart_path():
    cfg = _cfg()
    boxes = [Box(id=f"C{i}", length=300, width=300, height=300, weight=10.0)
             for i in range(4)]
    res = brkga_pack_v35(boxes, PALLET, cfg, seed=7, n_restarts=2, **_FAST)
    assert not res.unpacked
    _assert_recentred(res.pallets[0], PALLET)
    assert not validate(res, PALLET, cfg)


def test_solver_wrapper_group_path_recentres_each_pallet():
    cfg = _cfg(max_pallets=2)
    kit = [Box(id=f"K{i}", length=400, width=300, height=250, weight=10.0,
               group="kitchen") for i in range(4)]
    bath = [Box(id=f"B{i}", length=350, width=350, height=300, weight=12.0,
                group="bathroom") for i in range(4)]
    res = brkga_pack_v35(kit + bath, PALLET, cfg, max_pallets=2, seed=42,
                         **_FAST)
    assert not res.unpacked
    assert not validate(res, PALLET, cfg)
    for st in res.pallets:
        groups = {p.box.group for p in st.placements}
        assert len(groups) <= 2                        # co-location intact
        span = (max(p.x2 for p in st.placements)
                - min(p.x for p in st.placements))
        if span <= PALLET.length * 0.7:               # meaningfully underfilled
            assert min(p.x for p in st.placements) > 0.0
    # every group is on exactly one pallet
    for g in ("kitchen", "bathroom"):
        on = [st.pallet_id for st in res.pallets
              if any(p.box.group == g for p in st.placements)]
        assert len(on) == 1
