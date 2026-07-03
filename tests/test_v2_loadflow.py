"""Round-3 B4-1 (F22): v2's load commit must be exact on diamond DAGs.

The old recursive `_propagate_load` added a re-converged node's second
share but blocked its onward distribution (visited-set), so everything
BELOW a diamond junction undercounted forever — and `_load_bearing_ok`
(exact) then over-accepted later candidates against the stale cache.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pallet_packer")

from pallet_packer.models import (                                # noqa: E402
    ALL_ROTATIONS, Box, Pallet, PackerConfig, Placement,
)
from pallet_packer.packer import PalletState, regen_top_load       # noqa: E402

ROT = ALL_ROTATIONS[0]
PALLET = Pallet(length=400, width=400, height=1500)


def _cfg():
    return PackerConfig(support_ratio=0.5, require_centroid_supported=False,
                        enforce_load_bearing=True,
                        transitive_load_bearing=True)


def _tower(d_mlot=float("inf")):
    """Reconvergence BELOW a junction: E on A on {B1,B2} on C on D(floor).

    Exact transitive loads: A=6, B1=B2=7, C=20, D=24. The old code gave
    D=21 (one branch's onward flow dropped at the C junction).
    """
    D = Box(id="D", length=400, width=400, height=100, weight=5.0,
            max_load_on_top=d_mlot)
    C = Box(id="C", length=400, width=400, height=100, weight=4.0)
    B1 = Box(id="B1", length=200, width=400, height=100, weight=3.0)
    B2 = Box(id="B2", length=200, width=400, height=100, weight=3.0)
    A = Box(id="A", length=400, width=400, height=100, weight=8.0)
    E = Box(id="E", length=400, width=400, height=100, weight=6.0)
    return [Placement(box=D, rotation=ROT, x=0, y=0, z=0),
            Placement(box=C, rotation=ROT, x=0, y=0, z=100),
            Placement(box=B1, rotation=ROT, x=0, y=0, z=200),
            Placement(box=B2, rotation=ROT, x=200, y=0, z=200),
            Placement(box=A, rotation=ROT, x=0, y=0, z=300),
            Placement(box=E, rotation=ROT, x=0, y=0, z=400)]


EXACT = {"A": 6.0, "B1": 7.0, "B2": 7.0, "C": 20.0, "D": 24.0, "E": 0.0}


def test_regen_top_load_exact_on_diamond():
    st = PalletState(PALLET, "P001", _cfg())
    for p in _tower():
        st.placements.append(p)
        st.total_weight += p.box.weight
    regen_top_load(st)
    got = {p.box.id: st._top_load[id(p)] for p in st.placements}
    for k, v in EXACT.items():
        assert got[k] == pytest.approx(v, abs=1e-9), (k, got)


def test_commit_matches_regen_on_diamond():
    st = PalletState(PALLET, "P001", _cfg())
    for p in _tower():
        st._commit(p)
    got = {p.box.id: st._top_load[id(p)] for p in st.placements}
    for k, v in EXACT.items():
        assert got[k] == pytest.approx(v, abs=1e-9), (k, got)


def test_stale_cache_over_accept_is_fixed():
    """D has mlot=25 and truly carries 24. Adding F (2 kg) on E would push
    D to 26 > 25 — must be rejected. The old undercount (D=21 -> 23) let
    it through."""
    st = PalletState(PALLET, "P001", _cfg())
    for p in _tower(d_mlot=25.0):
        st._commit(p)
    F = Box(id="F", length=400, width=400, height=100, weight=2.0)
    cand = Placement(box=F, rotation=ROT, x=0, y=0, z=500)
    sups = st._supporters_of(cand)
    assert sups, "F must rest on E"
    assert st._load_bearing_ok(cand, sups) is False
    # a 0.9 kg box is fine (24 + 0.9 <= 25)
    G = Box(id="G", length=400, width=400, height=100, weight=0.9)
    cand2 = Placement(box=G, rotation=ROT, x=0, y=0, z=500)
    assert st._load_bearing_ok(cand2, st._supporters_of(cand2)) is True


# ------------------------------------------------- B4-2 (F21): under-fill
def _underfill_state(u_mlot):
    """S1 on floor; rider R half-supported by S1 (50%, sr=0.5). The
    candidate U slides under R's unsupported half at floor level: its top
    plane meets R's bottom, contact 400x200 == S1's, so U inherits
    R_out * a/(T_old + a) = 10 * 0.5 = 5 kg."""
    cfg = _cfg()
    st = PalletState(PALLET, "P001", cfg)
    S1 = Box(id="S1", length=400, width=200, height=100, weight=2.0)
    R = Box(id="R", length=400, width=400, height=100, weight=10.0)
    st._commit(Placement(box=S1, rotation=ROT, x=0, y=0, z=0))
    st._commit(Placement(box=R, rotation=ROT, x=0, y=0, z=100))
    U = Box(id="U", length=400, width=200, height=100, weight=3.0,
            max_load_on_top=u_mlot)
    return st, Placement(box=U, rotation=ROT, x=0, y=200, z=0)


def test_underfill_fragile_rejected():
    st, cand = _underfill_state(u_mlot=0.0)
    assert st._rider_inflow(cand) == pytest.approx(5.0)
    assert st.feasible(cand) is False


def test_underfill_capacity_boundary():
    st, cand = _underfill_state(u_mlot=4.9)      # 5.0 > 4.9 -> reject
    assert st.feasible(cand) is False
    st, cand = _underfill_state(u_mlot=5.1)      # 5.0 <= 5.1 -> accept
    assert st.feasible(cand) is True


def test_underfill_booked_and_visible_to_later_placements():
    st, cand = _underfill_state(u_mlot=10.0)
    assert st.feasible(cand) is True
    st._commit(cand)
    assert st._top_load[id(cand)] == pytest.approx(5.0)
    # A 6 kg box on top of U would make 11 > 10 -> rejected; 4 kg fits.
    heavy = Box(id="T1", length=400, width=200, height=100, weight=6.0)
    t1 = Placement(box=heavy, rotation=ROT, x=0, y=200, z=100)
    # t1 would also touch R's side plane? No: t1 z 100-200 sits beside R
    # (y 200-400 vs R y 0-400 overlap!) — R occupies (0..400, 0..400,
    # 100..200), so t1 would collide with R. Stack on U is impossible in
    # this geometry; check the load ceiling directly instead.
    assert st._load_bearing_ok(t1, [(cand, 400 * 200)]) is False
    light = Box(id="T2", length=400, width=200, height=100, weight=4.0)
    t2 = Placement(box=light, rotation=ROT, x=0, y=200, z=100)
    assert st._load_bearing_ok(t2, [(cand, 400 * 200)]) is True


def test_pallet_cap_tolerance_unified_round5():
    """R1 (round 5): validate must accept a v2-legal total inside the
    scale-aware tolerance band (limit 1e6 + 2e-4 was a spurious warning
    before — v2's feasible used load_tol, validate used absolute 1e-6)."""
    from pallet_packer.validate import validate
    pal = Pallet(length=1200, width=800, height=1500, max_weight=1e6)
    cfg = _cfg()
    from pallet_packer.packer import PackResult
    st = PalletState(pal, "P001", cfg)
    for i, w in enumerate((1e6 / 3 + 1e-4, 1e6 / 3, 1e6 / 3)):
        b = Box(id=f"w{i}", length=400, width=400, height=100, weight=w)
        st.placements.append(Placement(box=b, rotation=ROT,
                                       x=400 * i, y=0, z=0))
        st.total_weight += w
    res = PackResult(pallets=[st], unpacked=[])
    errs = [e for e in validate(res, pal, config=cfg) if "total weight" in e]
    assert errs == [], errs           # inside load_tol(1e6) = 1e-3
    # far beyond the tolerance must still fail
    b4 = Box(id="w4", length=400, width=400, height=100, weight=10.0)
    st.placements.append(Placement(box=b4, rotation=ROT, x=0, y=400, z=0))
    st.total_weight += 10.0
    errs = [e for e in validate(res, pal, config=cfg) if "total weight" in e]
    assert errs, "10 kg over the cap must still be flagged"
