"""Docker-tier tests for the hardened validator (hardening plan A2) and the
align pass dependents gate (A1). The validator is postprocess.py's safety
net, so its checks must mirror the engine's feasibility stack — no weaker
(the F1 probes), and no stricter than the weakest engine path (F15).
"""
from __future__ import annotations

import pytest

pp = pytest.importorskip("pallet_packer")

from pallet_packer import Box, Pallet, PackerConfig            # noqa: E402
from pallet_packer.models import Placement, Rotation           # noqa: E402
from pallet_packer.packer import PackResult, PalletState       # noqa: E402
from pallet_packer.postprocess import align_orientations_pass  # noqa: E402
from pallet_packer.validate import validate                    # noqa: E402

PALLET = Pallet(length=1200, width=800, height=1500, max_weight=float("inf"))


def _state(cfg, spec, pallet=PALLET):
    st = PalletState(pallet, "P001", cfg)
    for box, rot, x, y, z in spec:
        st.placements.append(Placement(box=box, rotation=rot, x=x, y=y, z=z))
        st.total_weight += box.weight
    return PackResult(pallets=[st], unpacked=[])


# ---------------------------------------------------------------------------
# A1: dependents gate — any deviant that supports another box is skipped
# ---------------------------------------------------------------------------
def test_align_skips_yaw_swap_under_dependent():
    # The F1 probe: dev's yaw swap (WLH->LWH, same dz) would pull the
    # footprint out from under T, stranding it at support 0. The gate must
    # skip dev entirely — even though the swap is height-preserving.
    cfg = PackerConfig(recenter_layout=True, align_orientations=True)
    sku = [Box(id=f"S{i}", length=400, width=300, height=200, weight=8.0)
           for i in range(3)]
    top = Box(id="T", length=100, width=100, height=100, weight=1.0)
    res = _state(cfg, [
        (sku[0], Rotation.LWH, 0.0, 0.0, 0.0),
        (sku[1], Rotation.LWH, 400.0, 0.0, 0.0),
        (sku[2], Rotation.WLH, 800.0, 0.0, 0.0),     # deviant, x 800-1100 y 0-400
        (top, Rotation.LWH, 1000.0, 300.0, 200.0),   # rests on dev's far corner
    ])
    st = res.pallets[0]
    align_orientations_pass(st, PALLET, cfg)
    dev = next(p for p in st.placements if p.box.id == "S2")
    assert dev.rotation == Rotation.WLH               # untouched
    assert not validate(res, PALLET, cfg)


# ---------------------------------------------------------------------------
# A2: hardened validator — each check that was missing before
# ---------------------------------------------------------------------------
def test_validate_flags_centroid_over_gap():
    # 80% supported (passes the ratio at sr=0.8) but the centroid sits over
    # the hole between the two supporters — engine rejects, validator must too.
    cfg = PackerConfig(support_ratio=0.8, require_centroid_supported=True)
    s1 = Box(id="A", length=400, width=400, height=200, weight=10.0)
    s2 = Box(id="B", length=400, width=400, height=200, weight=10.0)
    t = Box(id="T", length=500, width=300, height=100, weight=1.0)
    res = _state(cfg, [
        (s1, Rotation.LWH, 0.0, 0.0, 0.0),           # x 0-400
        (s2, Rotation.LWH, 500.0, 0.0, 0.0),          # x 500-900 (gap 400-500)
        (t, Rotation.LWH, 200.0, 0.0, 200.0),         # x 200-700, centroid x=450
    ])
    errs = validate(res, PALLET, cfg)
    assert any("centroid" in e for e in errs), errs
    cfg_no_centroid = PackerConfig(support_ratio=0.8,
                                   require_centroid_supported=False)
    assert not validate(res, PALLET, cfg_no_centroid)


def test_validate_flags_rfs_under_support():
    cfg = PackerConfig(support_ratio=0.5, require_centroid_supported=False)
    base = Box(id="B", length=360, width=300, height=200, weight=10.0)
    rfs = Box(id="R", length=400, width=300, height=200, weight=5.0,
              requires_full_support=True)
    res = _state(cfg, [
        (base, Rotation.LWH, 0.0, 0.0, 0.0),
        (rfs, Rotation.LWH, 0.0, 0.0, 200.0),         # 90% supported
    ])
    errs = validate(res, PALLET, cfg)
    assert any("R is floating" in e for e in errs), errs
    # The same support level is fine for a non-RFS box at sr=0.5.
    plain = Box(id="P", length=400, width=300, height=200, weight=5.0)
    res2 = _state(cfg, [
        (base, Rotation.LWH, 0.0, 0.0, 0.0),
        (plain, Rotation.LWH, 0.0, 0.0, 200.0),
    ])
    assert not validate(res2, PALLET, cfg)


def test_validate_flags_zero_contact_floater_at_sr0():
    # sr=0 + centroid ON (the service's support_zero shape): a box hovering
    # mid-air must be flagged — the old validator passed it.
    cfg = PackerConfig(support_ratio=0.0, require_centroid_supported=True)
    a = Box(id="A", length=300, width=300, height=200, weight=5.0)
    f = Box(id="F", length=300, width=300, height=200, weight=5.0)
    res = _state(cfg, [
        (a, Rotation.LWH, 0.0, 0.0, 0.0),
        (f, Rotation.LWH, 600.0, 0.0, 500.0),         # nothing below
    ])
    errs = validate(res, PALLET, cfg)
    assert any("floating (no contact)" in e for e in errs), errs


def test_validate_pure_geometric_mode_still_floats():
    # sr=0, centroid OFF, no RFS: the geometric decoders never enforce
    # support, so the validator must not either (BR results float legally).
    cfg = PackerConfig(support_ratio=0.0, require_centroid_supported=False,
                       enforce_load_bearing=False)
    a = Box(id="A", length=300, width=300, height=200, weight=0.0)
    f = Box(id="F", length=300, width=300, height=200, weight=0.0)
    res = _state(cfg, [
        (a, Rotation.LWH, 0.0, 0.0, 0.0),
        (f, Rotation.LWH, 600.0, 0.0, 500.0),
    ])
    assert not validate(res, PALLET, cfg)


def test_validate_load_model_is_direct_not_transitive():
    # F15 contract: A(mlot=5) carries B directly (4 kg <= 5) while C's 4 kg
    # rests on B only. The BRKGA decoders' direct model allows this, so the
    # validator must too — even though v2's transitive model would reject it.
    cfg = PackerConfig(support_ratio=0.8, require_centroid_supported=False)
    a = Box(id="A", length=300, width=300, height=200, weight=10.0,
            max_load_on_top=5.0)
    b = Box(id="B", length=300, width=300, height=200, weight=4.0)
    c = Box(id="C", length=300, width=300, height=200, weight=4.0)
    res = _state(cfg, [
        (a, Rotation.LWH, 0.0, 0.0, 0.0),
        (b, Rotation.LWH, 0.0, 0.0, 200.0),
        (c, Rotation.LWH, 0.0, 0.0, 400.0),
    ])
    assert not validate(res, PALLET, cfg)
    # Direct overload IS flagged: drop C onto A's level partner directly.
    heavy = Box(id="H", length=300, width=300, height=200, weight=6.0)
    res2 = _state(cfg, [
        (a, Rotation.LWH, 0.0, 0.0, 0.0),
        (heavy, Rotation.LWH, 0.0, 0.0, 200.0),       # 6 kg > mlot 5
    ])
    errs = validate(res2, PALLET, cfg)
    assert any("max_load_on_top" in e for e in errs), errs


def test_validate_explicit_cog_range_enforced():
    pal = Pallet(length=1200, width=800, height=1500,
                 max_weight=float("inf"), cog_x_range=(500.0, 700.0))
    cfg = PackerConfig()
    box = Box(id="A", length=300, width=300, height=300, weight=50.0)
    box2 = Box(id="B", length=300, width=300, height=300, weight=50.0)
    # Two boxes low on the deck -> combined CoG x=150 outside (500,700). With
    # >= 2 placements the engine's running-CoG reject WOULD have checked it, so
    # validate enforces it too.
    off = _state(cfg, [(box, Rotation.LWH, 0.0, 0.0, 0.0),
                       (box2, Rotation.LWH, 0.0, 400.0, 0.0)], pallet=pal)
    errs = validate(off, pal, cfg)
    assert any("CoG" in e for e in errs), errs
    centred = _state(cfg, [(box, Rotation.LWH, 450.0, 0.0, 0.0),
                           (box2, Rotation.LWH, 450.0, 300.0, 0.0)], pallet=pal)
    assert not validate(centred, pal, cfg)
    # F37 (round 8): a SINGLE off-range box is NOT flagged — the engines never
    # CoG-check the first box (v2 _cog_ok `not self.placements`; the JIT
    # new-bin path seeds it with no envelope check), so validate must not
    # reject an engine-legal lone corner box.
    single = _state(cfg, [(box, Rotation.LWH, 0.0, 0.0, 0.0)], pallet=pal)
    assert not validate(single, pal, cfg)
    # No explicit range -> the fraction default is NOT enforced (geometric
    # path never enforces it, so the validator must not either).
    assert not validate(
        _state(cfg, [(box, Rotation.LWH, 0.0, 0.0, 0.0)]), PALLET, cfg)
