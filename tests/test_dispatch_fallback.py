"""Round-3 A4-4 (F24): `_BATCH_AVAILABLE` must actually gate a working
scalar fallback.

Before round 3 the flag was set but never consulted: on any host without
the compiled Cython extensions, decode_population_fitness called the
Cython-only batch entries unconditionally and every constrained solve died
with NameError on generation 0 — while the module header promised a Numba
fallback. The fallback is the docstring's reference loop made literal, so
its plan must match the batch path exactly (realism off ⇒ bit-identical
fitness math)."""
from __future__ import annotations

import json

import pytest

pytest.importorskip("pallet_packer")

from pallet_packer import to_json                                  # noqa: E402
from pallet_packer.models import Box, Pallet, PackerConfig          # noqa: E402
from pallet_packer.brkga_v3_5 import brkga_pack_v35                 # noqa: E402
import pallet_packer._brkga_core.dispatch as dispatch               # noqa: E402

PALLET = Pallet(length=800, width=600, height=1200)


def _solve():
    boxes = [Box(id=f"b{i}", length=200, width=150 + 50 * (i % 2),
                 height=100 + 50 * (i % 3), weight=2.0 + i,
                 max_load_on_top=25.0) for i in range(6)]
    cfg = PackerConfig(support_ratio=0.8, require_centroid_supported=True,
                       enforce_load_bearing=True,
                       transitive_load_bearing=True,
                       realism_weight=0.0,       # exact fitness (no epsilon)
                       seed=7)
    res = brkga_pack_v35(boxes, PALLET, cfg, time_limit_s=1.5,
                         max_pallets=1, seed=7, use_v2_seed=False,
                         verbose=False)
    return json.dumps(to_json(res, PALLET), sort_keys=True)


def test_scalar_fallback_matches_batch_path(monkeypatch):
    plan_batch = _solve()
    monkeypatch.setattr(dispatch, "_BATCH_AVAILABLE", False)
    monkeypatch.setattr(dispatch, "_BATCH_FALLBACK_WARNED", False)
    plan_fallback = _solve()
    assert plan_fallback == plan_batch
