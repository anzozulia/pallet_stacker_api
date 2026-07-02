#!/usr/bin/env python3
"""Realism acceptance battery for the pallet-packer API.

Stdlib only (urllib + threads) so it runs on the host against a running stack,
in the style of scripts/loadtest.py. It submits 19 real-world palletizing
scenarios (single boxes, full layers, fragile goods, heavy/light mixes,
overhang, multi-pallet groups, seed variants), polls each to a terminal state
with 4 concurrent pollers, saves one record JSON per scenario (payload +
result + status) into --out, then analyzes every result in-process:

  metrics — floor coverage, boxes-on-floor, stack top, z-levels, load bbox,
            weighted CoG offset, per-SKU orientation counts (incl. per-z-level
            mixing), support ratios.
  flags   — TOWER, OFF_CENTER_COG, EDGE_HUGGING, MIXED_ORIENTATION,
            TALL_NARROW_STACK (plus HEAVY_OVER_LIGHT for the two mixed-weight
            scenarios).

Verdicts (acceptance): OFF_CENTER_COG, EDGE_HUGGING, TIPPED_IN_LAYER (a
same-SKU box with a different vertical extent inside one z-level — breaks the
layer top), and HEAVY_OVER_LIGHT FAIL a scenario, as does any non-done job
status. In-layer yaw mixing (legitimate interlocking), cross-layer
MIXED_ORIENTATION, and TOWER / TALL_NARROW_STACK are WARN-only: tower-flattening for mixed SKUs is expected to improve only
partially (decoder tie-breaks deferred). Exits 1 if any scenario FAILs.

The full analysis text (with top-down ASCII renders per z-level) is always
saved to <out>/<id>.txt; renders are printed to stdout only with --render.

Example:
  python3 scripts/realism_scenarios.py --base http://localhost:8000/api/v1 \
      --out results/realism --render
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import time
import urllib.request
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

POLL_INTERVAL_S = 1.5
POLL_CAP_S = 240.0
POLLERS = 4
# Scenarios whose story demands heavy boxes low: get the extra weight check.
HEAVY_LOW_SCENARIOS = ("heavy_light_mix", "warehouse_order_mix")
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

# ------------------------------------------------------------------ scenarios
# Dimensions in mm, weights in kg. Pallet height = loadable height above deck.

EUR = {"length": 1200, "width": 800, "height": 1500, "max_weight": 1000}
EUR_TALL = {"length": 1200, "width": 800, "height": 1800, "max_weight": 1000}
HALF = {"length": 1200, "width": 1000, "height": 1500, "max_weight": 1000}


def _boxes(prefix, n, length, width, height, weight, **kw):
    return [{"id": f"{prefix}{i + 1}", "length": length, "width": width,
             "height": height, "weight": weight, **kw} for i in range(n)]


SCENARIOS = [
    {
        "id": "single_box",
        "story": "One 25kg box shipped alone. A packer centers it on the pallet "
                 "for balanced forklift handling and even weight on the deck.",
        "expect": "Box at/near pallet center; NOT jammed into a corner.",
        "payload": {"boxes": _boxes("A", 1, 600, 400, 400, 25.0),
                    "pallet": EUR, "options": {"time_budget_s": 10}},
    },
    {
        "id": "two_identical",
        "story": "Two identical 20kg boxes. A packer puts them side by side, "
                 "same orientation, centered as a pair.",
        "expect": "Side-by-side on the floor, identical orientation, pair centered.",
        "payload": {"boxes": _boxes("A", 2, 600, 400, 400, 20.0),
                    "pallet": EUR, "options": {"time_budget_s": 10}},
    },
    {
        "id": "three_cubes",
        "story": "Three 350mm cube cartons, 15kg each. A packer lays them in a "
                 "flat row on the deck — nobody builds a 3-high tower of 3 boxes.",
        "expect": "All three at z=0, in a row, roughly centered.",
        "payload": {"boxes": _boxes("C", 3, 350, 350, 350, 15.0),
                    "pallet": HALF, "options": {"time_budget_s": 10}},
    },
    {
        "id": "six_cubes_underfill",
        "story": "Six 300mm cubes on a 1200x1000 pallet — the floor fits 12. A "
                 "packer spreads all six flat on the deck.",
        "expect": "All six at z=0 (single flat layer), no stacking, compact and centered.",
        "payload": {"boxes": _boxes("C", 6, 300, 300, 300, 10.0),
                    "pallet": HALF, "options": {"time_budget_s": 12}},
    },
    {
        "id": "ten_identical_fixcase",
        "story": "The exact case from the floor-first fix commit: 10 identical "
                 "400x300x250 boxes on 1200x800x1800.",
        "expect": "Flat 4x2 layer at z=0 plus 2 on top (per the fix's verified output).",
        "payload": {"boxes": _boxes("B", 10, 400, 300, 250, 8.0),
                    "pallet": EUR_TALL, "options": {"time_budget_s": 12}},
    },
    {
        "id": "full_layer_exact",
        "story": "Twelve 400x200x150 cartons tile the 1200x800 deck exactly "
                 "(3x4). A packer builds one clean full layer, all aligned.",
        "expect": "One complete layer at z=0, 100% floor coverage, uniform orientation.",
        "payload": {"boxes": _boxes("T", 12, 400, 200, 150, 5.0),
                    "pallet": {"length": 1200, "width": 800, "height": 1200, "max_weight": 800},
                    "options": {"time_budget_s": 15}},
    },
    {
        "id": "beverage_crates",
        "story": "32 beverage crates 400x300x300, 12kg each, EUR pallet. Classic "
                 "column/interlock stacking: 8 per layer (400x300 tiles 1200x800 "
                 "exactly), 4 full layers, aligned columns.",
        "expect": "8 crates/layer x 4 layers, full floor coverage, consistent "
                  "orientation per layer.",
        "payload": {"boxes": _boxes("K", 32, 400, 300, 300, 12.0),
                    "pallet": {"length": 1200, "width": 800, "height": 1600, "max_weight": 900},
                    "options": {"time_budget_s": 30}},
    },
    {
        "id": "heavy_light_mix",
        "story": "4 heavy 40kg cartons + 12 light 2kg cartons. A packer puts the "
                 "heavy ones flat on the deck spread over the area, lights on top.",
        "expect": "Heavies all at z=0 spread out; lights above/beside; CoG near center and low.",
        "payload": {"boxes": _boxes("H", 4, 600, 400, 300, 40.0)
                             + _boxes("L", 12, 300, 200, 200, 2.0),
                    "pallet": EUR_TALL, "options": {"time_budget_s": 25}},
    },
    {
        "id": "fragile_glassware",
        "story": "8 sturdy cartons + 4 fragile glassware boxes (nothing may go on "
                 "top). A packer builds the sturdy base and finishes with fragile on top.",
        "expect": "Fragile boxes on the top layer (or floor with nothing above), never buried.",
        "payload": {"boxes": _boxes("S", 8, 400, 300, 300, 10.0)
                             + _boxes("F", 4, 400, 300, 200, 4.0, max_load_on_top=0),
                    "pallet": EUR, "options": {"time_budget_s": 25}},
    },
    {
        "id": "this_side_up_wine",
        "story": "10 wine cases 300x250x450 marked THIS SIDE UP. Height axis must "
                 "stay vertical; a packer makes tight upright rows.",
        "expect": "All upright (height 450 vertical), tidy rows, floor-first.",
        "payload": {"boxes": _boxes("W", 10, 300, 250, 450, 14.0, rotations="this_side_up"),
                    "pallet": EUR, "options": {"time_budget_s": 20}},
    },
    {
        "id": "appliance_plus_small",
        "story": "A 65kg washing machine (this side up, nothing on top) plus 12 "
                 "small 3kg boxes. A packer puts the appliance against one side, "
                 "smalls beside it on the deck to balance the load.",
        "expect": "Appliance upright with nothing on top; smalls fill the remaining "
                  "floor; CoG reasonably central.",
        "payload": {"boxes": [{"id": "WM", "length": 700, "width": 700, "height": 1200,
                               "weight": 65.0, "rotations": "this_side_up",
                               "max_load_on_top": 0}]
                             + _boxes("S", 12, 350, 250, 200, 3.0),
                    "pallet": {"length": 1200, "width": 1000, "height": 1500, "max_weight": 600},
                    "options": {"time_budget_s": 25}},
    },
    {
        "id": "same_sku_rotation",
        "story": "Nine identical 500x300x250 cartons. A packer keeps one "
                 "orientation per layer — mixed rotations of the same SKU in one "
                 "layer read as sloppy and pack worse.",
        "expect": "Same orientation for all (or per-layer), coherent grid.",
        "payload": {"boxes": _boxes("R", 9, 500, 300, 250, 9.0),
                    "pallet": HALF, "options": {"time_budget_s": 20}},
    },
    {
        "id": "cog_two_heavy",
        "story": "Two 100kg machine parts on one pallet. A packer places them "
                 "symmetrically about the pallet center so the forklift load is balanced.",
        "expect": "Symmetric/balanced placement, combined CoG at pallet center.",
        "payload": {"boxes": _boxes("M", 2, 500, 400, 400, 100.0),
                    "pallet": HALF, "options": {"time_budget_s": 12}},
    },
    {
        "id": "warehouse_order_mix",
        "story": "A realistic 28-box e-commerce order: 7 SKUs of varied size and "
                 "weight, 2 fragile. Expect a stable, dense, layered build: big "
                 "heavy SKUs low, small light ones and fragile on top.",
        "expect": "Heavy low / light high, fragile on top, high utilization, coherent layers.",
        "payload": {"boxes": _boxes("BIG", 6, 600, 400, 300, 18.0)
                             + _boxes("MED", 4, 500, 400, 300, 15.0)
                             + _boxes("BOX", 6, 400, 300, 250, 8.0)
                             + _boxes("CUB", 4, 300, 300, 300, 10.0)
                             + _boxes("SML", 4, 350, 250, 150, 4.0)
                             + _boxes("FRG", 2, 250, 200, 150, 2.0, max_load_on_top=0)
                             + _boxes("LNG", 2, 600, 200, 200, 6.0),
                    "pallet": {"length": 1200, "width": 800, "height": 1800, "max_weight": 750},
                    "options": {"time_budget_s": 40}},
    },
    {
        "id": "overhang_case",
        "story": "Four 650x450 cartons on a 1200x800 pallet with 50mm overhang "
                 "allowed. Real overhang is symmetric (a bit over each edge), and "
                 "used only when needed.",
        "expect": "2x2 arrangement using modest overhang, roughly symmetric.",
        "payload": {"boxes": _boxes("O", 4, 650, 450, 400, 20.0),
                    "pallet": {"length": 1200, "width": 800, "height": 1500,
                               "max_weight": 800, "max_overhang": 50},
                    "options": {"time_budget_s": 15}},
    },
    {
        "id": "tall_and_flat",
        "story": "3 tall 200x200x800 lamps + 6 flat 400x300x150 cartons. A packer "
                 "groups the talls together (mutual support) and layers the flats.",
        "expect": "Talls upright clustered together; flats layered nearby; nothing "
                  "perched on a single tall item.",
        "payload": {"boxes": _boxes("TL", 3, 200, 200, 800, 12.0)
                             + _boxes("FL", 6, 400, 300, 150, 6.0),
                    "pallet": {"length": 1200, "width": 800, "height": 1600, "max_weight": 500},
                    "options": {"time_budget_s": 20}},
    },
    {
        "id": "seedvar_six_cubes_s7",
        "story": "six_cubes_underfill again at seed 7 — how much does layout "
                 "change on identical input?",
        "expect": "Qualitatively the same sensible flat layout as seed 42.",
        "payload": {"boxes": _boxes("C", 6, 300, 300, 300, 10.0),
                    "pallet": HALF, "options": {"time_budget_s": 12, "seed": 7}},
    },
    {
        "id": "seedvar_six_cubes_s123",
        "story": "six_cubes_underfill at seed 123.",
        "expect": "Qualitatively the same sensible flat layout as seed 42.",
        "payload": {"boxes": _boxes("C", 6, 300, 300, 300, 10.0),
                    "pallet": HALF, "options": {"time_budget_s": 12, "seed": 123}},
    },
    {
        "id": "multi_pallet_groups",
        "story": "Two room-kits (8 kitchen boxes, 8 bathroom boxes) on up to 2 "
                 "pallets. Groups must stay together; each pallet packed sensibly.",
        "expect": "One group per pallet (or clean split), flat sensible layers on each.",
        "payload": {"boxes": _boxes("KIT", 8, 400, 300, 250, 10.0, group="kitchen")
                             + _boxes("BTH", 8, 350, 350, 300, 12.0, group="bathroom"),
                    "pallet": EUR, "options": {"max_pallets": 2, "time_budget_s": 30}},
    },
]

# --------------------------------------------------------------- http helpers


def _post(base: str, path: str, body: dict, timeout: float = 30.0):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def _get(base: str, path: str, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(base + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _submit_and_poll(base: str, payload: dict, t0: float) -> dict:
    status, sub = _post(base, "/pack", payload)
    if status != 202:
        raise RuntimeError(f"submit got {status}: {sub}")
    job_id = sub["job_id"]
    while True:
        time.sleep(POLL_INTERVAL_S)
        st = _get(base, f"/jobs/{job_id}")
        if st["status"] in ("done", "failed", "timeout"):
            return st
        if time.time() - t0 > POLL_CAP_S:
            return {"status": "poll_timeout"}


def run_one(base: str, out_dir: pathlib.Path, sc: dict) -> dict:
    """Submit one scenario, poll it to a terminal state, save the record JSON."""
    t0 = time.time()
    try:
        st = _submit_and_poll(base, sc["payload"], t0)
    except (OSError, RuntimeError, ValueError) as exc:
        st = {"status": "error", "error": str(exc)}
    rec = {"scenario": sc["id"], "story": sc["story"], "expect": sc["expect"],
           "payload": sc["payload"], "status": st["status"],
           "result": st.get("result"), "error": st.get("error"),
           "wall_s": round(time.time() - t0, 1)}
    (out_dir / f"{sc['id']}.json").write_text(json.dumps(rec, indent=1))
    print(f"  {sc['id']}: {st['status']} in {rec['wall_s']}s", flush=True)
    return rec


# ------------------------------------------------------------------- analysis


def _sku_of(item: dict, payload: dict):
    """SKU signature = sorted original dims + weight (from the input payload)."""
    for b in payload["boxes"]:
        if b["id"] == item["item_id"]:
            return (tuple(sorted((b["length"], b["width"], b["height"]))), b["weight"])
    return ("?",)


def _render_level(items, length, width, z_lo, z_hi, letters) -> str:
    """Top-down map of boxes whose vertical span intersects [z_lo, z_hi)."""
    cols = 48
    rows = max(6, round(cols * width / length / 2))  # chars are ~2x tall
    grid = [["." for _ in range(cols)] for _ in range(rows)]
    for it in items:
        x, y, z = it["position"]["x"], it["position"]["y"], it["position"]["z"]
        dl, dw, dh = it["dimensions"]["L"], it["dimensions"]["W"], it["dimensions"]["H"]
        if z >= z_hi or z + dh <= z_lo:
            continue
        c0 = max(0, min(cols - 1, int(x / length * cols)))
        c1 = max(0, min(cols, math.ceil((x + dl) / length * cols)))
        r0 = max(0, min(rows - 1, int(y / width * rows)))
        r1 = max(0, min(rows, math.ceil((y + dw) / width * rows)))
        ch = letters[it["item_id"]]
        for r in range(r0, r1):
            for c in range(c0, c1):
                grid[r][c] = ch
    return "\n".join("".join(r) for r in grid)


def analyze(rec: dict) -> dict:
    """Metrics + weirdness flags + acceptance verdict for one scenario record."""
    lines = [f"SCENARIO {rec['scenario']}  (status={rec['status']}, wall={rec['wall_s']}s)",
             f"STORY: {rec['story']}", f"EXPECTED: {rec['expect']}", ""]
    flags: list[str] = []
    fails: list[str] = []
    warns: list[str] = []
    if rec["status"] != "done" or not rec.get("result"):
        lines.append(f"NO RESULT: {rec.get('error')}")
        fails.append(f"status={rec['status']}")
        return {"lines": lines, "flags": flags, "fails": fails, "warns": warns}
    res, payload = rec["result"], rec["payload"]
    summ = res["input_summary"]
    lines.append(f"packed={summ['items_packed']} unpacked={summ['items_unpacked']} "
                 f"pallets={summ['pallets_used']} util={summ['total_volume_utilisation']:.3f}")
    if res.get("unpacked_items"):
        lines.append("UNPACKED: " + ", ".join(u["item_id"] for u in res["unpacked_items"]))

    for p in res["pallets"]:
        length = p["dimensions"]["L"]
        width = p["dimensions"]["W"]
        height = p["dimensions"]["H"]
        items = p["items"]
        if not items:
            continue
        lines += ["", f"--- pallet {p['pallet_id']}  {length}x{width}x{height}  "
                      f"util={p['utilisation']:.3f}  total_weight={p['total_weight']}"]
        letters = {}
        for i, it in enumerate(sorted(items, key=lambda t: t["item_id"])):
            letters[it["item_id"]] = _ALPHABET[i % len(_ALPHABET)]
        lines.append("legend: " + "  ".join(f"{v}={k}" for k, v in letters.items()))

        # --- geometry metrics ------------------------------------------------
        z_starts = sorted({it["position"]["z"] for it in items})
        floor_items = [it for it in items if it["position"]["z"] == 0]
        floor_area = sum(it["dimensions"]["L"] * it["dimensions"]["W"] for it in floor_items)
        floor_cov = floor_area / (length * width)
        top = max((it["position"]["z"] + it["dimensions"]["H"] for it in items), default=0)
        n_above = len(items) - len(floor_items)
        # load bounding box + weighted centroid
        x0 = min(it["position"]["x"] for it in items)
        x1 = max(it["position"]["x"] + it["dimensions"]["L"] for it in items)
        y0 = min(it["position"]["y"] for it in items)
        y1 = max(it["position"]["y"] + it["dimensions"]["W"] for it in items)
        wtot = sum(it["weight"] for it in items) or 1.0
        cgx = sum((it["position"]["x"] + it["dimensions"]["L"] / 2) * it["weight"]
                  for it in items) / wtot
        cgy = sum((it["position"]["y"] + it["dimensions"]["W"] / 2) * it["weight"]
                  for it in items) / wtot
        offx, offy = (cgx - length / 2) / (length / 2), (cgy - width / 2) / (width / 2)
        lines.append(f"metrics: floor_coverage={floor_cov:.0%}  "
                     f"boxes_on_floor={len(floor_items)}/{len(items)}  "
                     f"stack_top={top}mm  z_levels={z_starts}")
        lines.append(f"         load_bbox=({x0}-{x1})x({y0}-{y1}) of {length}x{width}  "
                     f"weighted_CoG_offset=({offx:+.0%} x, {offy:+.0%} y) of half-pallet")
        # orientation consistency per SKU / per level
        by_sku = defaultdict(list)
        for it in items:
            by_sku[_sku_of(it, payload)].append(it)
        for sku, its in by_sku.items():
            if len(its) < 2:
                continue
            orients = defaultdict(int)
            for it in its:
                orients[it["orientation"]["name"]] += 1
            if len(orients) > 1:
                # Two severities. A TIPPED box inside a layer (different
                # vertical extent H at the same z) breaks the layer's top
                # surface — that is the real-world defect and a FAIL. Pure
                # yaw variation within a layer (same H, L/W swapped) is
                # legitimate interlocking — WARN only.
                per_level = defaultdict(set)
                per_level_h = defaultdict(set)
                for it in its:
                    per_level[it["position"]["z"]].add(it["orientation"]["name"])
                    per_level_h[it["position"]["z"]].add(it["dimensions"]["H"])
                mixed_lvls = [z for z, o in per_level.items() if len(o) > 1]
                tipped_lvls = [z for z, h in per_level_h.items() if len(h) > 1]
                lines.append(f"         SKU {sku[0]}: orientations {dict(orients)}"
                             + (f"  MIXED within z-levels {mixed_lvls}" if mixed_lvls
                                else "  (uniform per level)"))
                flags.append(f"MIXED_ORIENTATION: SKU {sku[0]} uses {len(orients)} orientations"
                             + (" inside a single layer" if mixed_lvls else " across layers"))
                if tipped_lvls:
                    fails.append("TIPPED_IN_LAYER")
                elif mixed_lvls:
                    warns.append("MIXED_ORIENTATION(in-layer yaw)")
                else:
                    warns.append("MIXED_ORIENTATION(cross-layer)")
        # support / fragile
        for it in items:
            sup = it.get("support_ratio")
            if it["position"]["z"] > 0 and sup is not None and sup < 0.999:
                lines.append(f"         {it['item_id']}: support_ratio={sup}")
        # --- weirdness flags -------------------------------------------------
        min_h = min(it["dimensions"]["H"] for it in items)
        if n_above > 0 and floor_cov < 0.5:
            flags.append(f"TOWER: {n_above} box(es) stacked above z=0 while only "
                         f"{floor_cov:.0%} of the floor is used")
            warns.append("TOWER")
        if abs(offx) > 0.25 or abs(offy) > 0.25:
            flags.append(f"OFF_CENTER_COG: weighted CoG offset ({offx:+.0%}, {offy:+.0%}) "
                         f"— load is biased toward one corner/edge")
            fails.append("OFF_CENTER_COG")
        if (x0 == 0 and x1 < length * 0.75) or (y0 == 0 and y1 < width * 0.75):
            flags.append(f"EDGE_HUGGING: load bbox ({x0}-{x1})x({y0}-{y1}) hugs the (0,0) "
                         f"corner of the {length}x{width} deck instead of being centered")
            fails.append("EDGE_HUGGING")
        if top > 2.5 * min_h and floor_cov < 0.6:
            flags.append(f"TALL_NARROW_STACK: stack height {top}mm on "
                         f"{floor_cov:.0%} floor coverage")
            warns.append("TALL_NARROW_STACK")
        # heavy-must-sit-low check (only for the mixed-weight scenarios)
        if rec["scenario"] in HEAVY_LOW_SCENARIOS:
            above = [it for it in items if it["position"]["z"] > 0]
            if floor_items and above:
                max_above = max(it["weight"] for it in above)
                min_floor = min(it["weight"] for it in floor_items)
                tot_above = sum(it["weight"] for it in above)
                tot_floor = sum(it["weight"] for it in floor_items)
                if max_above > 2 * min_floor and tot_above > tot_floor:
                    flags.append(
                        f"HEAVY_OVER_LIGHT: heaviest box above z=0 is {max_above}kg vs "
                        f"lightest floor box {min_floor}kg (>2x) and weight above z=0 "
                        f"({tot_above}kg) exceeds floor weight ({tot_floor}kg)")
                    fails.append("HEAVY_OVER_LIGHT")

        # --- renders ---------------------------------------------------------
        for zi, z in enumerate(z_starts):
            z_next = z_starts[zi + 1] if zi + 1 < len(z_starts) else top
            probe_hi = z + 1  # slice just above this start level
            lines += [f"top-down at z={z} (boxes occupying height "
                      f"{z}..{max(z_next, probe_hi)}):",
                      _render_level(items, length, width, z, probe_hi, letters)]

    lines += ["", "WEIRDNESS FLAGS:" if flags else "WEIRDNESS FLAGS: none (mechanically)"]
    lines += [f"  ! {f}" for f in flags]
    return {"lines": lines, "flags": flags,
            "fails": list(dict.fromkeys(fails)), "warns": list(dict.fromkeys(warns))}


# ------------------------------------------------------------------------ cli


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Realism acceptance battery: 19 scenarios, metrics, "
                    "weirdness flags, PASS/FAIL verdicts.")
    ap.add_argument("--base", default="http://localhost:8000/api/v1",
                    help="API base URL (default: %(default)s)")
    ap.add_argument("--out", default="results/realism",
                    help="directory for record JSON + analysis txt (default: %(default)s)")
    ap.add_argument("--render", action="store_true",
                    help="also print the full analysis (with ASCII renders) to stdout")
    args = ap.parse_args()

    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[realism] submitting {len(SCENARIOS)} scenarios to {args.base} "
          f"({POLLERS} concurrent pollers)", flush=True)
    with ThreadPoolExecutor(max_workers=POLLERS) as ex:
        records = list(ex.map(lambda sc: run_one(args.base, out_dir, sc), SCENARIOS))

    verdicts = []
    for rec in records:
        a = analyze(rec)
        text = "\n".join(a["lines"])
        (out_dir / f"{rec['scenario']}.txt").write_text(text)
        if args.render:
            print("\n" + text, flush=True)
        verdicts.append((rec, a))

    print(f"\n{'scenario':<24} {'status':<9} {'wall':>7}  {'verdict':<7} notes")
    n_fail = 0
    for rec, a in verdicts:
        if a["fails"]:
            n_fail += 1
            verdict = "FAIL"
            notes = "; ".join(a["fails"] + [f"warn:{w}" for w in a["warns"]])
        elif a["warns"]:
            verdict, notes = "WARN", "; ".join(a["warns"])
        else:
            verdict, notes = "PASS", ""
        print(f"{rec['scenario']:<24} {rec['status']:<9} {rec['wall_s']:>6}s  "
              f"{verdict:<7} {notes}")
    print(f"\n{len(verdicts) - n_fail}/{len(verdicts)} scenarios acceptable; "
          f"reports in {out_dir}/", flush=True)
    if n_fail:
        print(f"FAILED: {n_fail} scenario(s) violate realism acceptance criteria")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
