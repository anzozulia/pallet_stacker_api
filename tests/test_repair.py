"""Round-3 A4-1: the post-solve repair backstop (F23 / ADR D17).

repair_load_violations must deterministically strip the riders feeding an
overloaded carrier (topmost first, never leaving a hovering box) until the
plan validates clean; adapter.solve must run validate -> repair and surface
everything in `warnings` + `load_limit_repair` reasons, and add NOTHING on
clean solves."""
from __future__ import annotations

import pytest

pytest.importorskip("pallet_packer")

from pallet_packer.models import (                                # noqa: E402
    ALL_ROTATIONS, Box, Pallet, PackerConfig, Placement,
)
from pallet_packer.packer import PackResult, PalletState          # noqa: E402
from pallet_packer.repair import repair_load_violations           # noqa: E402
from pallet_packer.validate import validate                       # noqa: E402
from pallet_api.solver import adapter                             # noqa: E402

ROT = ALL_ROTATIONS[0]  # identity orientation
PALLET = Pallet(length=800, width=400, height=1500)


def _cfg(transitive=True):
    return PackerConfig(support_ratio=0.8, require_centroid_supported=False,
                        enforce_load_bearing=True,
                        transitive_load_bearing=transitive)


def _state(config, placements):
    st = PalletState(PALLET, "P001", config)
    for p in placements:
        st.placements.append(p)
        st.total_weight += p.box.weight
    return st


def _p(box, x, y, z):
    return Placement(box=box, rotation=ROT, x=x, y=y, z=z)


def _overloaded_result(config):
    """The round-3 diamond: 2 bases (mlot 17) + spanning mid (mlot 20) +
    four 8 kg tops directly on the mid — mid carries 32, bases 24 each."""
    b0 = Box(id="base0", length=400, width=400, height=200, weight=20.0,
             max_load_on_top=17.0)
    b1 = Box(id="base1", length=400, width=400, height=200, weight=20.0,
             max_load_on_top=17.0)
    mid = Box(id="mid", length=800, width=400, height=200, weight=16.0,
              max_load_on_top=20.0)
    tops = [Box(id=f"top{i}", length=200, width=200, height=100, weight=8.0)
            for i in range(4)]
    pls = [_p(b0, 0, 0, 0), _p(b1, 400, 0, 0), _p(mid, 0, 0, 200),
           _p(tops[0], 0, 0, 400), _p(tops[1], 200, 0, 400),
           _p(tops[2], 0, 200, 400), _p(tops[3], 200, 200, 400)]
    return PackResult(pallets=[_state(config, pls)], unpacked=[])


def test_repair_strips_until_valid_and_is_minimal_enough():
    cfg = _cfg(transitive=True)
    result = _overloaded_result(cfg)
    assert validate(result, PALLET, config=cfg)          # broken before
    result, actions = repair_load_violations(result, PALLET, cfg)
    assert validate(result, PALLET, config=cfg) == []    # clean after
    assert actions                                       # something was done
    # Only tops needed to go: mid(16) alone on the bases is 8 kg/base <= 17,
    # and the mid itself tolerates 2 tops (16 <= 20) but the bases cap the
    # column first (16+16=32 split 16/16 <= 17 with 2 tops... recompute:
    # with k tops: mid carries 8k; bases carry (16+8k)/2 each.
    # mid ok: 8k <= 20 -> k <= 2; bases ok: (16+8k)/2 <= 17 -> k <= 2.25.
    # So exactly 2 tops must have been stripped.
    removed = {b.id for b in result.unpacked}
    assert len(removed) == 2 and all(r.startswith("top") for r in removed)
    # bases and mid stay placed
    placed = {p.box.id for st in result.pallets for p in st.placements}
    assert {"base0", "base1", "mid"} <= placed


def test_repair_is_deterministic():
    cfg = _cfg(transitive=True)
    r1, a1 = repair_load_violations(_overloaded_result(cfg), PALLET, cfg)
    r2, a2 = repair_load_violations(_overloaded_result(cfg), PALLET, cfg)
    assert a1 == a2
    assert [b.id for b in r1.unpacked] == [b.id for b in r2.unpacked]


def test_repair_never_leaves_hovering_boxes():
    """Stripping a mid-stack box would strand what rests on it; repair must
    take the TOPMOST feeder. Column: base(mlot=5) <- mid(10kg) <- top(3kg)."""
    cfg = _cfg(transitive=True)
    base = Box(id="base", length=200, width=200, height=100, weight=1.0,
               max_load_on_top=5.0)
    mid = Box(id="mid", length=200, width=200, height=100, weight=10.0)
    top = Box(id="top", length=200, width=200, height=100, weight=3.0)
    pls = [_p(base, 0, 0, 0), _p(mid, 0, 0, 100), _p(top, 0, 0, 200)]
    result = PackResult(pallets=[_state(cfg, pls)], unpacked=[])
    result, actions = repair_load_violations(result, PALLET, cfg)
    assert validate(result, PALLET, config=cfg) == []
    # top (3) went first, then mid still overloads (10 > 5) and goes too.
    assert [b.id for b in result.unpacked] == ["top", "mid"]


def test_repair_noop_on_valid_plan():
    cfg = _cfg(transitive=True)
    box = Box(id="solo", length=200, width=200, height=100, weight=5.0,
              max_load_on_top=0.0)
    result = PackResult(pallets=[_state(cfg, [_p(box, 0, 0, 0)])],
                        unpacked=[])
    result, actions = repair_load_violations(result, PALLET, cfg)
    assert actions == []
    assert len(result.pallets[0].placements) == 1


def test_repair_direct_mode():
    """With transitive off, the direct model governs: 4 tops directly on the
    mid (32 > 20) must be cut down to 2 (16 <= 20); bases see only the mid's
    own 16 kg split (8 <= 17) so they never trigger."""
    cfg = _cfg(transitive=False)
    result = _overloaded_result(cfg)
    result, actions = repair_load_violations(result, PALLET, cfg)
    assert validate(result, PALLET, config=cfg) == []
    removed = {b.id for b in result.unpacked}
    assert len(removed) == 2 and all(r.startswith("top") for r in removed)


# --------------------------------------------------------------- adapter e2e
def _payload():
    return {
        "boxes": [{"id": "A", "length": 200, "width": 200, "height": 100,
                   "weight": 5.0}],
        "pallet": {"length": 800, "width": 400, "height": 1500},
        "options": {"time_budget_s": 2},
    }


_CFG = {"max_boxes": 500, "soft_budget_s": 90.0, "default_max_pallets": 1,
        "default_seed": 42, "population_size": 40, "n_populations": 1,
        "patience": 30, "n_modes": 6, "recenter": True,
        "align_orientations": True, "realism_weight": 1.0,
        "transitive_load": True}


def test_adapter_repairs_and_warns_on_invalid_plan(monkeypatch):
    def fake_solver(boxes, pallet, config, **kw):
        return _overloaded_result(config)

    monkeypatch.setattr(adapter, "brkga_pack_v35", fake_solver)
    payload = _payload()
    payload["boxes"] = [  # ids must exist for conservation, dims arbitrary
        {"id": i, "length": 200, "width": 200, "height": 100, "weight": 1.0}
        for i in ["base0", "base1", "mid", "top0", "top1", "top2", "top3"]]
    out = adapter.solve(payload, _CFG)
    assert out.get("warnings"), "warnings must surface the repair"
    assert any(w.startswith("validation:") for w in out["warnings"])
    assert any(w.startswith("repair:") for w in out["warnings"])
    repaired = [u for u in out["unpacked_items"]
                if u["reason"] == "load_limit_repair"]
    assert len(repaired) == 2
    # served geometry is the repaired one: 5 items placed
    assert out["input_summary"]["items_packed"] == 5


def test_adapter_clean_solve_has_no_warnings():
    out = adapter.solve(_payload(), _CFG)
    assert "warnings" not in out
    assert out["input_summary"]["items_packed"] == 1


def test_diamond_solves_physically_clean_end_to_end():
    """Round-3 flagship regression: the diamond instance that was served
    LIVE at 1.6x overload (mid carrying 32 kg on mlot=20) must now solve to
    a physically valid plan with no warnings — through the real solver."""
    payload = {
        "boxes": ([{"id": f"base{i}", "length": 400, "width": 400,
                    "height": 200, "weight": 20.0, "max_load_on_top": 17.0}
                   for i in range(2)]
                  + [{"id": "mid", "length": 800, "width": 400,
                      "height": 200, "weight": 16.0, "max_load_on_top": 20.0}]
                  + [{"id": f"top{i}", "length": 200, "width": 200,
                      "height": 100, "weight": 8.0} for i in range(4)]),
        "pallet": {"length": 800, "width": 400, "height": 1500},
        "options": {"time_budget_s": 4, "seed": 42},
    }
    out = adapter.solve(payload, _CFG)
    assert "warnings" not in out, out.get("warnings")
    # independent physics check: recompute transitive loads from geometry
    items = [it for pal in out["pallets"] for it in pal["items"]]
    w = {b["id"]: b["weight"] for b in payload["boxes"]}
    lim = {b["id"]: b.get("max_load_on_top") for b in payload["boxes"]}
    inflow = {it["item_id"]: 0.0 for it in items}
    for it in sorted(items, key=lambda q: -q["position"]["z"]):
        z = it["position"]["z"]
        out_w = w[it["item_id"]] + inflow[it["item_id"]]
        if z <= 0:
            continue
        sups = []
        for o in items:
            if o is it:
                continue
            if abs((o["position"]["z"] + o["dimensions"]["H"]) - z) > 1e-6:
                continue
            ox = (min(it["position"]["x"] + it["dimensions"]["L"],
                      o["position"]["x"] + o["dimensions"]["L"])
                  - max(it["position"]["x"], o["position"]["x"]))
            oy = (min(it["position"]["y"] + it["dimensions"]["W"],
                      o["position"]["y"] + o["dimensions"]["W"])
                  - max(it["position"]["y"], o["position"]["y"]))
            if ox > 0 and oy > 0:
                sups.append((o["item_id"], ox * oy))
        tot = sum(a for _, a in sups)
        for oid, a in sups:
            inflow[oid] += out_w * (a / tot)
    for iid, load in inflow.items():
        if lim[iid] is not None:
            assert load <= lim[iid] + 1e-6, (iid, load, lim[iid])
