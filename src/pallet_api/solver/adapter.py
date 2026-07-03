"""The ONLY module that imports the pallet_packer core (D10). Translates a
validated request dict into core objects, runs gate -> brkga_pack_v35 -> to_json.
Plain dicts in/out so it is picklable for the solve subprocess.
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Tuple

from pallet_packer import (
    Box, Pallet, PackerConfig, ALL_ROTATIONS, THIS_SIDE_UP, NO_ROTATION,
    check_packing_input, to_json,
)
from pallet_packer.brkga_v3_5 import brkga_pack_v35
from pallet_packer.repair import repair_load_violations
from pallet_packer.validate import validate

logger = logging.getLogger(__name__)

_ROT = {"all": ALL_ROTATIONS, "this_side_up": THIS_SIDE_UP, "none": NO_ROTATION}


def _box(d: Dict[str, Any]) -> Box:
    mlot = d.get("max_load_on_top")
    group = d.get("group")
    if isinstance(group, str) and not group.strip():
        # ""/whitespace-only means "no group" — passing it through would
        # co-locate every such box onto ONE pallet as a real group (F10).
        group = None
    return Box(
        id=str(d["id"]),
        length=d["length"], width=d["width"], height=d["height"],
        weight=float(d.get("weight") or 0.0),
        max_load_on_top=(math.inf if mlot is None else float(mlot)),
        allowed_rotations=list(_ROT.get(d.get("rotations", "all"), ALL_ROTATIONS)),
        group=group,
        requires_full_support=bool(d.get("requires_full_support", False)),
    )


def _pallet(d: Dict[str, Any]) -> Pallet:
    mw = d.get("max_weight")
    return Pallet(
        length=d["length"], width=d["width"], height=d["height"],
        max_weight=(math.inf if mw is None else float(mw)),
        max_overhang=d.get("max_overhang", 0) or 0,
    )


def build_inputs(payload: Dict[str, Any]) -> Tuple[List[Box], Pallet]:
    return [_box(b) for b in payload["boxes"]], _pallet(payload["pallet"])


def validate_request(payload: Dict[str, Any], *, max_boxes: int) -> None:
    """Run the input gate; raises pallet_packer.PackingInputError on violation."""
    boxes, pallet = build_inputs(payload)
    check_packing_input(boxes, pallet, max_boxes=max_boxes)


def _config(payload: Dict[str, Any],
            cfg: Dict[str, Any] | None = None) -> PackerConfig:
    cfg = cfg or {}
    opts = payload.get("options") or {}
    overhang = float(payload["pallet"].get("max_overhang") or 0) > 0
    # Sensible service defaults: stability ON (support + centroid), fragility
    # respected; CoG envelope OFF by default (advanced, would over-reject).
    # Realism layer (D14) ON by default: recentred loads, aligned same-SKU
    # orientations, and a heavy-low/anti-tower fitness gradient.
    return PackerConfig(
        support_ratio=float(opts.get("support_ratio", 0.8)),
        require_centroid_supported=True,
        enforce_load_bearing=True,
        cog_envelope_fraction=1.0,
        cog_check_min_load_fraction=1.0,
        allow_pallet_overhang=overhang,
        recenter_layout=bool(cfg.get("recenter", True)),
        align_orientations=bool(cfg.get("align_orientations", True)),
        realism_weight=float(cfg.get("realism_weight", 1.0)),
        transitive_load_bearing=bool(cfg.get("transitive_load", True)),
    )


def _json_safe(o: Any) -> Any:
    """Make the core's result strict-JSON safe. Unlimited caps surface as inf
    internally (None -> math.inf in _box/_pallet); the core echoes them in
    to_json, but starlette's JSONResponse serialises with allow_nan=False, so a
    raw inf/nan would 500 the result fetch. Map non-finite floats back to None
    (= "unlimited", symmetric with the input contract)."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


def solve(payload: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Full solve: gate -> BRKGA -> to_json. Returns the core's result dict
    (sanitised to be strict-JSON safe — see _json_safe)."""
    boxes, pallet = build_inputs(payload)
    check_packing_input(boxes, pallet, max_boxes=cfg["max_boxes"])  # backstop
    opts = payload.get("options") or {}
    budget = float(opts.get("time_budget_s") or cfg["soft_budget_s"])
    budget = max(1.0, min(budget, cfg["soft_budget_s"]))            # clamp to ceiling
    max_pallets = int(opts.get("max_pallets") or cfg["default_max_pallets"])
    seed = int(cfg["default_seed"] if opts.get("seed") is None else opts["seed"])
    # Bigger caller budgets buy more polish. The realism fitness gives local
    # search a real gradient, so its budget is no longer a fixed no-op 4s.
    ls_budget = min(8.0, max(2.0, 0.25 * budget))
    # F18 mitigation (hardening round 2): force the v2 warm-start for small
    # instances even without constraints — the driver's auto-gate only seeds
    # v2 when constraints exist, but v2's layer/block builder tiles exact-fit
    # instances that the greedy BRKGA rotation argmax deterministically
    # breaks (a perfect 2x2x2 tiling packed 7/8 on every seed). The deadline
    # (half the budget) bounds it.
    n_small = len(payload["boxes"]) <= 60          # tunable
    if n_small:
        # Round 3 (F28): the driver silently skips the v2 seed when
        # 0.5*budget < 1 s, which negates the mitigation exactly where it
        # matters. Small instances finish in well under 2 s anyway, so
        # floor their budget instead of losing the seed. (Group solves
        # split the budget per pallet and can still lose the seed on very
        # group-heavy requests — documented in the API contract.)
        budget = max(budget, 2.0)
    config = _config(payload, cfg)
    result = brkga_pack_v35(
        boxes, pallet, config,
        time_limit_s=budget, max_pallets=max_pallets, seed=seed,
        population_size=cfg["population_size"], n_populations=cfg["n_populations"],
        patience=cfg["patience"], n_modes=cfg["n_modes"],
        local_search_budget_s=ls_budget,
        use_v2_seed=True if n_small else None,
        validate_input=True, verbose=False,
    )
    # Round 3 (F23 / ADR D17): never ship a plan that fails replay
    # validation silently. The engines are believed correct after the
    # round-3 load-model fixes; this is the last-resort backstop for
    # residual bugs: deterministically strip the riders feeding any
    # overloaded carrier into `unpacked` (reason: load_limit_repair) and
    # surface everything in a `warnings` field.
    warnings_out: List[str] = []
    errors = validate(result, pallet, config)
    if errors:
        logger.error("solve produced an invalid plan (%d violation(s)); "
                     "repairing: %s", len(errors), "; ".join(errors[:5]))
        pre_ids = {b.id for b in result.unpacked}
        result, actions = repair_load_violations(result, pallet, config)
        repaired_ids = {b.id for b in result.unpacked} - pre_ids
        residual = validate(result, pallet, config)
        warnings_out = ([f"validation: {e}" for e in errors]
                        + [f"repair: {a}" for a in actions]
                        + [f"unrepaired: {e}" for e in residual])
    else:
        repaired_ids = set()
    out = _json_safe(to_json(result, pallet))
    if warnings_out:
        out["warnings"] = warnings_out
        for u in out.get("unpacked_items", []):
            if u.get("item_id") in repaired_ids:
                u["reason"] = "load_limit_repair"
    return out
