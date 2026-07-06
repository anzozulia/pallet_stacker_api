"""Round-6 (F31 / ADR D19): the multi-pallet fitness objective.

Pre-fix the fitness (base + realism, scalar + batch) masked to pallets[0]:
for max_pallets>1 without groups the optimizer PROVABLY could not tell "10
boxes packed on pallet 1" from "those 10 boxes dropped" (0.975 both ways —
the round-6 evaluation's reproduced blindness case). The multibin objective
is a strict dominance hierarchy: unpacked volume >> pallet count >> realism,
gated to be byte-identical at max_pallets == 1.
"""
from __future__ import annotations

import math

import pytest

pp = pytest.importorskip("pallet_packer")

import numpy as np                                              # noqa: E402

from pallet_packer import Box, Pallet, PackerConfig             # noqa: E402
from pallet_packer.models import ALL_ROTATIONS, Placement       # noqa: E402
from pallet_packer.packer import PackResult, PalletState        # noqa: E402
from pallet_packer.brkga_v3_fast import _fitness_pallet1        # noqa: E402
from pallet_packer._brkga_core.realism import (                 # noqa: E402
    build_realism_context,
)

ROT = ALL_ROTATIONS[0]
PALLET = Pallet(length=1000, width=1000, height=1000, max_weight=math.inf)
CFG = PackerConfig()


def _boxes(n, length=400, width=400, height=90, weight=1.0):
    return [Box(id=f"b{i}", length=length, width=width, height=height,
                weight=weight, allowed_rotations=list(ALL_ROTATIONS))
            for i in range(n)]


def _st(pid, boxes, z0=0):
    st = PalletState(PALLET, pid, CFG)
    for i, b in enumerate(boxes):
        st.placements.append(Placement(box=b, rotation=ROT,
                                       x=0, y=0, z=z0 + i * b.height))
        st.total_weight += b.weight
    return st


def test_blindness_case_now_discriminates():
    """The round-6 repro: pallet 0 fixed, 10 boxes either packed on pallet 1
    or all unpacked — pre-fix both scored 0.975; now packed must win."""
    bxs = _boxes(11, length=500, width=500, height=100)
    packed = PackResult(pallets=[_st("P001", bxs[:1]), _st("P002", bxs[1:])],
                        unpacked=[])
    dropped = PackResult(pallets=[_st("P001", bxs[:1])], unpacked=bxs[1:])
    for realism in (None, build_realism_context(bxs, PALLET, PackerConfig(
            realism_weight=1.0))):
        f_packed = _fitness_pallet1(packed, PALLET, realism=realism,
                                    max_pallets=2)
        f_dropped = _fitness_pallet1(dropped, PALLET, realism=realism,
                                     max_pallets=2)
        assert f_packed < f_dropped - 1e-9, (realism, f_packed, f_dropped)


def test_pallet_count_preference():
    """Equal packed volume: fewer pallets must win by ~beta."""
    bxs = _boxes(4)
    one = PackResult(pallets=[_st("P001", bxs)], unpacked=[])
    two = PackResult(pallets=[_st("P001", bxs[:2]), _st("P002", bxs[2:])],
                     unpacked=[])
    f1 = _fitness_pallet1(one, PALLET, max_pallets=4)
    f2 = _fitness_pallet1(two, PALLET, max_pallets=4)
    cap = 1000.0 ** 3
    beta = 0.5 * (400 * 400 * 90) / cap
    assert f1 < f2 - 1e-9
    assert f2 - f1 == pytest.approx(beta, rel=1e-9)


def test_extra_pallet_beats_dropping():
    """2 pallets all-packed must beat 1 pallet + drops, even when the
    2-pallet layout has the worse realism (heavy split high vs low)."""
    bxs = _boxes(6, weight=5.0)
    spill = PackResult(pallets=[_st("P001", bxs[:4]), _st("P002", bxs[4:])],
                       unpacked=[])
    drops = PackResult(pallets=[_st("P001", bxs[:4])], unpacked=bxs[4:])
    ctx = build_realism_context(bxs, PALLET, PackerConfig(realism_weight=1.0))
    assert ctx is not None
    f_spill = _fitness_pallet1(spill, PALLET, realism=ctx, max_pallets=2)
    f_drops = _fitness_pallet1(drops, PALLET, realism=ctx, max_pallets=2)
    assert f_spill < f_drops - 1e-9


def test_realism_cannot_flip_pallet_count():
    """eps is halved at apply time so a full realism swing (<1) stays under
    beta: same packed set on 1 vs 2 pallets keeps the 1-pallet winner even
    with realism maximally against it."""
    bxs = _boxes(4, weight=10.0)
    # 1 pallet: a 4-high tower (worst-ish realism: tall, heavy high).
    one = PackResult(pallets=[_st("P001", bxs)], unpacked=[])
    # 2 pallets: two flat 2-stacks (better realism).
    two = PackResult(pallets=[_st("P001", bxs[:2]), _st("P002", bxs[2:])],
                     unpacked=[])
    ctx = build_realism_context(bxs, PALLET, PackerConfig(realism_weight=1.0))
    f1 = _fitness_pallet1(one, PALLET, realism=ctx, max_pallets=4)
    f2 = _fitness_pallet1(two, PALLET, realism=ctx, max_pallets=4)
    assert f1 < f2 - 1e-9, "realism flipped a pallet-count decision"


def test_gate_identity_at_max_pallets_1():
    """max_pallets=1 must be the EXACT historical value (==, not approx),
    with and without realism."""
    bxs = _boxes(5)
    res = PackResult(pallets=[_st("P001", bxs)], unpacked=[])
    ctx = build_realism_context(bxs, PALLET, PackerConfig(realism_weight=1.0))
    for realism in (None, ctx):
        legacy = _fitness_pallet1(res, PALLET, realism=realism)
        gated = _fitness_pallet1(res, PALLET, realism=realism, max_pallets=1)
        assert legacy == gated


@pytest.mark.parametrize("max_pallets", [1, 4])
@pytest.mark.parametrize("with_realism", [False, True])
def test_scalar_batch_parity_multibin(max_pallets, with_realism):
    """Parity on a small pallet that genuinely opens several bins AND drops
    boxes at the cap; also pins the no-empty-PalletState invariant that
    makes scalar len(pallets) == batch n_bins_out_all."""
    from pallet_packer._brkga_core.dispatch import (
        decode_auto_mode, decode_population_fitness)
    from pallet_packer._brkga_core.blocks import (
        enumerate_best_block_per_sku, enumerate_top_k_blocks_per_sku)
    from pallet_packer._brkga_core.precompute import (
        precompute_box_dims_and_sku, precompute_constraint_arrays,
        precompute_cog_envelope)

    rng = np.random.default_rng(7)
    boxes = []
    for i in range(14):
        d = rng.integers(150, 420, size=3)
        boxes.append(Box(id=f"B{i}", length=int(d[0]), width=int(d[1]),
                         height=int(d[2]), weight=float(rng.integers(1, 20))))
    pallet = Pallet(length=500, width=450, height=400, max_weight=math.inf)
    config = PackerConfig(support_ratio=0.8, require_centroid_supported=True,
                          realism_weight=1.0 if with_realism else 0.0)

    n_rots_arr, dims_all, sku_id = precompute_box_dims_and_sku(boxes)
    L, W, H = 500, 450, 400
    best_block = enumerate_best_block_per_sku(
        boxes, sku_id, n_rots_arr, dims_all, L, W, H)
    top_k = enumerate_top_k_blocks_per_sku(
        boxes, sku_id, n_rots_arr, dims_all, L, W, H, k_top=8)
    weights, mlot, rfs, pmw, _ = precompute_constraint_arrays(boxes, pallet)
    cx0, cx1, cy0, cy1, cml, cact = precompute_cog_envelope(pallet, config)
    kw = dict(n_modes=6, sku_id_per_box=sku_id, sku_best_block=best_block,
              sku_top_k_blocks=top_k, mode_cdf=None, weights=weights,
              mlot=mlot, rfs=rfs, pallet_max_weight=pmw,
              has_constraints=True, support_ratio=0.8, require_centroid=1,
              cog_x_min=cx0, cog_x_max=cx1, cog_y_min=cy0, cog_y_max=cy1,
              cog_min_load_frac=cml, cog_active=cact, max_overhang=0.0)
    ctx = build_realism_context(
        boxes, pallet, config, dims_all=dims_all, sku_id_per_box=sku_id,
        weights_arr=weights, n_rots_arr=n_rots_arr)
    assert (ctx is not None) == with_realism

    n = len(boxes)
    pop = np.random.default_rng(99).random((12, 2 * n + 1))
    batch = decode_population_fitness(
        pop, boxes, pallet, config, n_rots_arr, dims_all,
        max_pallets=max_pallets, use_multi_decoder=True, realism=ctx, **kw)
    saw_multibin = saw_drop = False
    for i in range(pop.shape[0]):
        res = decode_auto_mode(pop[i], boxes, pallet, config, n_rots_arr,
                               dims_all, max_pallets=max_pallets, **kw)
        assert all(st.placements for st in res.pallets), \
            "decoder emitted an empty PalletState"
        saw_multibin |= len(res.pallets) > 1
        saw_drop |= bool(res.unpacked)
        scalar = _fitness_pallet1(res, pallet, realism=ctx,
                                  max_pallets=max_pallets)
        assert abs(scalar - batch[i]) < 1e-12, (
            f"chrom {i}: scalar={scalar!r} batch={batch[i]!r}")
    if max_pallets == 4:
        assert saw_multibin, "instance never opened a second bin"
    assert saw_drop, "instance never dropped a box (cap too loose)"
