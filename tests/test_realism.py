"""Docker-tier tests for the secondary realism fitness (D14).

The centerpiece is scalar/batch parity: the driver's best-updates consume the
vectorised batch fitness while the polish phases consume the scalar one, and
both feed the same 1e-9 strict-improvement comparisons — a divergence between
the two paths would corrupt the search. Everything else checks the term's
semantics: bounded loss, heavy-low preference, orientation consistency,
zero-weight fallback, and the flag-OFF bit-identity guarantees.
"""
from __future__ import annotations

import math

import pytest

pp = pytest.importorskip("pallet_packer")

import numpy as np                                              # noqa: E402

from pallet_packer import Box, Pallet, PackerConfig             # noqa: E402
from pallet_packer.models import Placement, Rotation            # noqa: E402
from pallet_packer.packer import PackResult, PalletState        # noqa: E402
from pallet_packer.brkga_v3_fast import _fitness_pallet1        # noqa: E402
from pallet_packer._brkga_core.blocks import (                  # noqa: E402
    enumerate_best_block_per_sku,
    enumerate_top_k_blocks_per_sku,
)
from pallet_packer._brkga_core.chromosome import (              # noqa: E402
    make_informed_chromosomes,
)
from pallet_packer._brkga_core.dispatch import (                # noqa: E402
    decode_auto_mode,
    decode_population_fitness,
)
from pallet_packer._brkga_core.precompute import (              # noqa: E402
    precompute_box_dims_and_sku,
    precompute_constraint_arrays,
    precompute_cog_envelope,
)
from pallet_packer._brkga_core.realism import (                 # noqa: E402
    build_realism_context,
    realism_scalar,
)


def _instance(seed: int, n: int = 18, constrained: bool = True):
    rng = np.random.default_rng(seed)
    boxes = []
    for i in range(n):
        d = rng.integers(100, 400, size=3)
        kw = {}
        if constrained:
            kw["weight"] = float(rng.integers(1, 30))
            if i % 5 == 0:
                kw["max_load_on_top"] = float(rng.integers(0, 50))
        boxes.append(Box(id=f"B{i}", length=int(d[0]), width=int(d[1]),
                         height=int(d[2]), **kw))
    pallet = Pallet(length=1200, width=1000, height=1500,
                    max_weight=800.0 if constrained else math.inf)
    return boxes, pallet


def _decode_kwargs(boxes, pallet, config, n_modes=6):
    """Mirror the driver's decoder parameter derivation exactly."""
    n_rots_arr, dims_all, sku_id = precompute_box_dims_and_sku(boxes)
    L, W, H = (int(round(pallet.length)), int(round(pallet.width)),
               int(round(pallet.height)))
    best_block = enumerate_best_block_per_sku(
        boxes, sku_id, n_rots_arr, dims_all, L, W, H)
    top_k = enumerate_top_k_blocks_per_sku(
        boxes, sku_id, n_rots_arr, dims_all, L, W, H, k_top=8)
    weights, mlot, rfs, pmw, has_cstr = \
        precompute_constraint_arrays(boxes, pallet)
    cx0, cx1, cy0, cy1, cml, cact = precompute_cog_envelope(pallet, config)
    stability = (float(config.support_ratio) > 0.0
                 or bool(config.require_centroid_supported))
    overhang = (bool(config.allow_pallet_overhang)
                and float(pallet.max_overhang) > 0.0)
    use_cstr = bool(has_cstr or stability or cact or overhang)
    if use_cstr:
        sr = float(config.support_ratio)
        rc = 1 if config.require_centroid_supported else 0
        ca = 1 if cact else 0
        mo = float(pallet.max_overhang) if config.allow_pallet_overhang else 0.0
    else:
        sr, rc, ca, mo = 0.0, 0, 0, 0.0
        cx0, cx1, cy0, cy1, cml = -1e18, 1e18, -1e18, 1e18, 0.0
    arrays = dict(n_rots_arr=n_rots_arr, dims_all=dims_all,
                  sku_id_per_box=sku_id, weights_arr=weights)
    kw = dict(n_modes=n_modes, sku_id_per_box=sku_id,
              sku_best_block=best_block, sku_top_k_blocks=top_k,
              mode_cdf=None, weights=weights, mlot=mlot,
              pallet_max_weight=pmw, has_constraints=use_cstr,
              support_ratio=sr, rfs=rfs, require_centroid=rc,
              cog_x_min=cx0, cog_x_max=cx1, cog_y_min=cy0, cog_y_max=cy1,
              cog_min_load_frac=cml, cog_active=ca, max_overhang=mo)
    return arrays, kw


@pytest.mark.parametrize("constrained", [True, False])
@pytest.mark.parametrize("with_realism", [True, False])
def test_scalar_batch_parity(constrained, with_realism):
    boxes, pallet = _instance(seed=11 if constrained else 12,
                              constrained=constrained)
    config = PackerConfig(
        support_ratio=0.8 if constrained else 0.0,
        require_centroid_supported=constrained,
        cog_envelope_fraction=1.0, cog_check_min_load_fraction=1.0,
        realism_weight=1.0 if with_realism else 0.0)
    arrays, kw = _decode_kwargs(boxes, pallet, config)
    ctx = build_realism_context(
        boxes, pallet, config, dims_all=arrays["dims_all"],
        sku_id_per_box=arrays["sku_id_per_box"],
        weights_arr=arrays["weights_arr"], n_rots_arr=arrays["n_rots_arr"])
    assert (ctx is not None) == with_realism
    n = len(boxes)
    rng = np.random.default_rng(99)
    pop = rng.random((16, 2 * n + 1))
    batch = decode_population_fitness(
        pop, boxes, pallet, config, arrays["n_rots_arr"], arrays["dims_all"],
        max_pallets=1, use_multi_decoder=True, realism=ctx, **kw)
    for i in range(pop.shape[0]):
        res = decode_auto_mode(
            pop[i], boxes, pallet, config, arrays["n_rots_arr"],
            arrays["dims_all"], max_pallets=1, **kw)
        scalar = _fitness_pallet1(res, pallet, realism=ctx)
        assert abs(scalar - batch[i]) < 1e-12, (
            f"chrom {i}: scalar={scalar!r} batch={batch[i]!r}")


# ---------------------------------------------------------------------------
# term semantics on hand-built layouts
# ---------------------------------------------------------------------------
def _hand_result(boxes, pallet, spec):
    cfg = PackerConfig()
    st = PalletState(pallet, "P001", cfg)
    for bi, rot, x, y, z in spec:
        st.placements.append(Placement(box=boxes[bi], rotation=rot,
                                       x=x, y=y, z=z))
        st.total_weight += boxes[bi].weight
    return PackResult(pallets=[st], unpacked=[])


def test_heavy_low_beats_heavy_high():
    pallet = Pallet(length=1200, width=800, height=1500)
    heavy = Box(id="H", length=400, width=400, height=300, weight=40.0)
    light = Box(id="L", length=400, width=400, height=300, weight=2.0)
    boxes = [heavy, light]
    config = PackerConfig(realism_weight=1.0)
    ctx = build_realism_context(boxes, pallet, config)
    good = _hand_result(boxes, pallet, [(0, Rotation.LWH, 0, 0, 0),
                                        (1, Rotation.LWH, 0, 0, 300)])
    bad = _hand_result(boxes, pallet, [(1, Rotation.LWH, 0, 0, 0),
                                       (0, Rotation.LWH, 0, 0, 300)])
    f_good = _fitness_pallet1(good, pallet, realism=ctx)
    f_bad = _fitness_pallet1(bad, pallet, realism=ctx)
    assert f_good < f_bad - 1e-9          # same volume, better height moment


def test_flat_beats_tower():
    pallet = Pallet(length=1200, width=800, height=1500)
    boxes = [Box(id=f"C{i}", length=300, width=300, height=300, weight=10.0)
             for i in range(3)]
    config = PackerConfig(realism_weight=1.0)
    ctx = build_realism_context(boxes, pallet, config)
    flat = _hand_result(boxes, pallet, [(i, Rotation.LWH, 300 * i, 0, 0)
                                        for i in range(3)])
    tower = _hand_result(boxes, pallet, [(i, Rotation.LWH, 0, 0, 300 * i)
                                         for i in range(3)])
    assert (_fitness_pallet1(flat, pallet, realism=ctx)
            < _fitness_pallet1(tower, pallet, realism=ctx) - 1e-9)


def test_flat_orientation_beats_tipped():
    # OI is keyed on height class (dz): a TIPPED box inside a layer is
    # penalised, while a pure yaw variation (interlocking) is not.
    pallet = Pallet(length=1200, width=800, height=1500)
    boxes = [Box(id=f"R{i}", length=400, width=300, height=250, weight=8.0)
             for i in range(3)]
    config = PackerConfig(realism_weight=1.0)
    ctx = build_realism_context(boxes, pallet, config)
    flat = _hand_result(boxes, pallet, [(i, Rotation.LWH, 400 * i, 0, 0)
                                        for i in range(3)])
    tipped = _hand_result(boxes, pallet, [(0, Rotation.LWH, 0, 0, 0),
                                          (1, Rotation.LWH, 400, 0, 0),
                                          (2, Rotation.LHW, 800, 0, 0)])
    assert (_fitness_pallet1(flat, pallet, realism=ctx)
            < _fitness_pallet1(tipped, pallet, realism=ctx) - 1e-9)
    # Yaw-only mixing shares the height class -> same OI as uniform. The
    # tipped layout is taller too, so isolate OI by comparing yaw vs flat.
    yaw = _hand_result(boxes, pallet, [(0, Rotation.LWH, 0, 0, 0),
                                       (1, Rotation.LWH, 400, 0, 0),
                                       (2, Rotation.WLH, 800, 0, 0)])
    assert abs(_fitness_pallet1(yaw, pallet, realism=ctx)
               - _fitness_pallet1(flat, pallet, realism=ctx)) < 1e-12


def test_bounded_loss_never_drops_a_box():
    # A packing that drops the smallest box must ALWAYS lose to a full
    # packing, no matter how bad the full packing's realism score is.
    pallet = Pallet(length=1200, width=800, height=1500)
    boxes = [Box(id="BIG", length=600, width=400, height=400, weight=20.0),
             Box(id="SMALL", length=100, width=100, height=100, weight=1.0)]
    config = PackerConfig(realism_weight=1.0)
    ctx = build_realism_context(boxes, pallet, config)
    # Worst-realism full pack: small box carries the big one at the top.
    full = _hand_result(boxes, pallet, [(1, Rotation.LWH, 0, 0, 0),
                                        (0, Rotation.LWH, 0, 0, 1100)])
    dropped = _hand_result(boxes[:1], pallet, [(0, Rotation.LWH, 0, 0, 0)])
    dropped.unpacked = [boxes[1]]
    assert (_fitness_pallet1(full, pallet, realism=ctx)
            < _fitness_pallet1(dropped, pallet, realism=ctx))


def test_one_orientation_flip_clears_acceptance_band():
    # eps * (one OI unit at n=500, at the OI term weight) must clear the
    # 1e-9 strict-improvement threshold, else polish moves that fix a
    # single rotation are dead.
    from pallet_packer._brkga_core.realism import _W_OI
    pallet = Pallet(length=1200, width=800, height=1500)
    boxes = [Box(id=f"B{i}", length=100, width=100, height=100, weight=1.0)
             for i in range(500)]
    ctx = build_realism_context(boxes, pallet,
                                PackerConfig(realism_weight=1.0))
    assert ctx is not None
    assert ctx.eps * (_W_OI * (1.0 / 500)) > 1e-9


def test_zero_weight_falls_back_to_volume():
    pallet = Pallet(length=1200, width=800, height=1500)
    boxes = [Box(id=f"C{i}", length=300, width=300, height=300, weight=0.0)
             for i in range(2)]
    config = PackerConfig(realism_weight=1.0)
    ctx = build_realism_context(boxes, pallet, config)
    flat = _hand_result(boxes, pallet, [(0, Rotation.LWH, 0, 0, 0),
                                        (1, Rotation.LWH, 300, 0, 0)])
    stack = _hand_result(boxes, pallet, [(0, Rotation.LWH, 0, 0, 0),
                                         (1, Rotation.LWH, 0, 0, 300)])
    r_flat, r_stack = (realism_scalar(flat, ctx), realism_scalar(stack, ctx))
    assert math.isfinite(r_flat) and math.isfinite(r_stack)
    assert r_flat < r_stack               # volume-weighted HM still prefers flat


# ---------------------------------------------------------------------------
# flag-OFF guarantees
# ---------------------------------------------------------------------------
def test_realism_none_keeps_fitness_identical():
    boxes, pallet = _instance(seed=5)
    res = _hand_result(boxes[:3], pallet,
                       [(i, Rotation.LWH, 400 * i, 0, 0) for i in range(3)])
    assert (_fitness_pallet1(res, pallet)
            == _fitness_pallet1(res, pallet, realism=None))


def test_realism_weight_is_clamped_to_bounded_loss():
    # Hardening plan A4: eps must never exceed 0.5*min_vol/cap, or dropping
    # the smallest box becomes profitable and the bounded-loss guarantee is
    # silently void. Weights above 1.0 clamp to 1.0.
    pallet = Pallet(length=1200, width=800, height=1500)
    boxes = [Box(id="BIG", length=600, width=400, height=400, weight=20.0),
             Box(id="SMALL", length=100, width=100, height=100, weight=1.0)]
    ctx1 = build_realism_context(boxes, pallet,
                                 PackerConfig(realism_weight=1.0))
    ctx4 = build_realism_context(boxes, pallet,
                                 PackerConfig(realism_weight=4.0))
    assert ctx4.eps == ctx1.eps                      # clamped
    cap = 1200 * 800 * 1500
    assert ctx4.eps <= 0.5 * (100 ** 3) / cap + 1e-18
    # Bounded-loss property must hold even at the absurd weight.
    full = _hand_result(boxes, pallet, [(1, Rotation.LWH, 0, 0, 0),
                                        (0, Rotation.LWH, 0, 0, 1100)])
    dropped = _hand_result(boxes[:1], pallet, [(0, Rotation.LWH, 0, 0, 0)])
    dropped.unpacked = [boxes[1]]
    assert (_fitness_pallet1(full, pallet, realism=ctx4)
            < _fitness_pallet1(dropped, pallet, realism=ctx4))


def test_duplicate_box_ids_disable_realism():
    # C7/F9: the scalar path maps placements to rows BY ID, the batch path is
    # positional — duplicates would silently desync the two objectives. The
    # service gate enforces unique ids; un-gated library callers get the term
    # disabled instead of corrupted.
    pallet = Pallet(length=1200, width=800, height=1500)
    boxes = [Box(id="X", length=300, width=200, height=150, weight=2.0),
             Box(id="X", length=300, width=200, height=150, weight=2.0)]
    assert build_realism_context(
        boxes, pallet, PackerConfig(realism_weight=1.0)) is None


def test_realism_weight_zero_disables_context():
    boxes, pallet = _instance(seed=6)
    assert build_realism_context(boxes, pallet, PackerConfig()) is None
    assert build_realism_context(
        boxes, pallet, PackerConfig(realism_weight=0.0)) is None


def test_weight_desc_seed_is_gated():
    boxes, pallet = _instance(seed=7)
    n_rots_arr, _, _ = precompute_box_dims_and_sku(boxes)
    w = np.array([b.weight for b in boxes])
    legacy = make_informed_chromosomes(boxes, n_rots_arr, seed=1)
    ungated = make_informed_chromosomes(boxes, n_rots_arr, seed=1,
                                        weights=w,
                                        include_weight_order=False)
    gated = make_informed_chromosomes(boxes, n_rots_arr, seed=1, weights=w,
                                      include_weight_order=True)
    assert len(ungated) == len(legacy)     # default callers see no change
    assert len(gated) == len(legacy) + 1   # heavy-first appended when gated


# ---------------------------------------------------------------------------
# end-to-end behavior through the service adapter (realism defaults ON)
# ---------------------------------------------------------------------------
from pallet_api.solver import adapter   # noqa: E402  (conftest adds src/)


def _payload(boxes, pallet, budget=6, **opts):
    return {"boxes": boxes, "pallet": pallet,
            "options": {"time_budget_s": budget, **opts}}


def test_adapter_recentres_underfilled(fast_cfg):
    boxes = [{"id": f"C{i}", "length": 300, "width": 300, "height": 300,
              "weight": 10.0} for i in range(6)]
    res = adapter.solve(
        _payload(boxes, {"length": 1200, "width": 1000, "height": 1500}),
        fast_cfg)
    p0 = res["pallets"][0]
    assert abs(p0["cog"]["x"] - 600) <= 60      # within 10% of half-deck
    assert abs(p0["cog"]["y"] - 500) <= 50


def test_adapter_uniform_orientation_per_level(fast_cfg):
    boxes = [{"id": f"R{i}", "length": 500, "width": 300, "height": 250,
              "weight": 9.0} for i in range(9)]
    res = adapter.solve(
        _payload(boxes, {"length": 1200, "width": 1000, "height": 1500}),
        fast_cfg)
    levels: dict = {}
    for it in res["pallets"][0]["items"]:
        dims = (it["dimensions"]["L"], it["dimensions"]["W"],
                it["dimensions"]["H"])
        levels.setdefault(it["position"]["z"], set()).add(dims)
    assert all(len(v) == 1 for v in levels.values()), levels


def test_adapter_heavies_on_floor(fast_cfg):
    boxes = ([{"id": f"H{i}", "length": 600, "width": 400, "height": 300,
               "weight": 40.0} for i in range(2)]
             + [{"id": f"L{i}", "length": 300, "width": 200, "height": 200,
                 "weight": 2.0} for i in range(4)])
    res = adapter.solve(
        _payload(boxes, {"length": 1200, "width": 800, "height": 1500}),
        fast_cfg)
    items = res["pallets"][0]["items"]
    heavy_z = [it["position"]["z"] for it in items
               if it["item_id"].startswith("H")]
    assert heavy_z and all(z == 0 for z in heavy_z)
    summ = res["input_summary"]
    assert summ["items_packed"] + summ["items_unpacked"] == 6


def test_adapter_deterministic_with_realism(fast_cfg):
    boxes = [{"id": f"C{i}", "length": 300, "width": 300, "height": 300,
              "weight": 10.0} for i in range(6)]
    payload = _payload(boxes, {"length": 1200, "width": 1000, "height": 1500})
    assert adapter.solve(payload, fast_cfg) == adapter.solve(payload, fast_cfg)
