"""The ONLY module that imports the pallet_packer core (D10). Translates a
validated request dict into core objects, runs gate -> brkga_pack_v35 -> to_json.
Plain dicts in/out so it is picklable for the solve subprocess.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from pallet_packer import (
    Box, Pallet, PackerConfig, ALL_ROTATIONS, THIS_SIDE_UP, NO_ROTATION,
    check_packing_input, to_json,
)
from pallet_packer.brkga_v3_5 import brkga_pack_v35

_ROT = {"all": ALL_ROTATIONS, "this_side_up": THIS_SIDE_UP, "none": NO_ROTATION}


def _box(d: Dict[str, Any]) -> Box:
    mlot = d.get("max_load_on_top")
    return Box(
        id=str(d["id"]),
        length=d["length"], width=d["width"], height=d["height"],
        weight=float(d.get("weight") or 0.0),
        max_load_on_top=(math.inf if mlot is None else float(mlot)),
        allowed_rotations=list(_ROT.get(d.get("rotations", "all"), ALL_ROTATIONS)),
        group=d.get("group"),
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


def _config(payload: Dict[str, Any]) -> PackerConfig:
    opts = payload.get("options") or {}
    overhang = float(payload["pallet"].get("max_overhang") or 0) > 0
    # Sensible service defaults: stability ON (support + centroid), fragility
    # respected; CoG envelope OFF by default (advanced, would over-reject).
    return PackerConfig(
        support_ratio=float(opts.get("support_ratio", 0.8)),
        require_centroid_supported=True,
        enforce_load_bearing=True,
        cog_envelope_fraction=1.0,
        cog_check_min_load_fraction=1.0,
        allow_pallet_overhang=overhang,
    )


def solve(payload: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Full solve: gate -> BRKGA -> to_json. Returns the core's result dict."""
    boxes, pallet = build_inputs(payload)
    check_packing_input(boxes, pallet, max_boxes=cfg["max_boxes"])  # backstop
    opts = payload.get("options") or {}
    budget = float(opts.get("time_budget_s") or cfg["soft_budget_s"])
    budget = max(1.0, min(budget, cfg["soft_budget_s"]))            # clamp to ceiling
    max_pallets = int(opts.get("max_pallets") or cfg["default_max_pallets"])
    seed = int(cfg["default_seed"] if opts.get("seed") is None else opts["seed"])
    result = brkga_pack_v35(
        boxes, pallet, _config(payload),
        time_limit_s=budget, max_pallets=max_pallets, seed=seed,
        population_size=cfg["population_size"], n_populations=cfg["n_populations"],
        patience=cfg["patience"], n_modes=cfg["n_modes"],
        validate_input=True, verbose=False,
    )
    return to_json(result, pallet)
