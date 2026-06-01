#!/usr/bin/env python3
"""Phase 6 load-test harness for the pallet-packer API.

Stdlib only (urllib + threads) so it runs on the host against a running stack.
Two modes:

  curve  — submit jobs ONE AT A TIME for a list of box counts; measures the
           single-solve cost curve (is a solve time-bounded or convergence-bounded?).
  burst  — submit N jobs near-simultaneously, poll all to completion concurrently;
           measures throughput, end-to-end latency percentiles, queue wait, and
           the OBSERVED max concurrency (running intervals) — i.e. the scaling model.

Examples:
  python3 scripts/loadtest.py curve --boxes 10,50,100,200,500 --budget 90 \
      --out results/lt_curve.json
  python3 scripts/loadtest.py burst --jobs 24 --boxes 60 --budget 90 \
      --concurrency 24 --label 4wx2t --out results/lt_burst_4wx2t.json
"""
from __future__ import annotations

import argparse
import json
import random
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------- request gen

# A small fixed SKU catalogue (heterogeneous, integer dims, ~10% fragile) so the
# load is representative of a real mixed pallet rather than uniform boxes.
def _catalogue(rng: random.Random):
    skus = []
    for s in range(12):
        skus.append({
            "length": rng.randint(100, 400),
            "width": rng.randint(80, 350),
            "height": rng.randint(80, 300),
            "weight": float(rng.randint(1, 30)),
            # first 2 SKUs are fragile (max_load_on_top = 0)
            "max_load_on_top": 0.0 if s < 2 else float(rng.randint(20, 80)),
        })
    return skus


def make_request(n_boxes: int, seed: int, budget: float, max_pallets: int = 1) -> dict:
    rng = random.Random(seed)
    skus = _catalogue(rng)
    boxes = []
    for i in range(n_boxes):
        sku = skus[i % len(skus)]
        boxes.append({
            "id": f"B{i:04d}",
            "length": sku["length"], "width": sku["width"], "height": sku["height"],
            "weight": sku["weight"], "max_load_on_top": sku["max_load_on_top"],
            "rotations": "all",
        })
    return {
        "boxes": boxes,
        "pallet": {"length": 1200, "width": 1000, "height": 1500, "max_weight": 1500},
        "options": {"max_pallets": max_pallets, "time_budget_s": budget, "seed": 42},
    }


# ---------------------------------------------------------------- http helpers

def _post(base: str, path: str, body: dict, timeout: float = 30.0):
    data = json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data,
                                 headers={"content-type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def _get(base: str, path: str, timeout: float = 30.0):
    req = urllib.request.Request(base + path, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode())


def submit(base: str, body: dict) -> str:
    status, payload = _post(base, "/pack", body)
    if status != 202:
        raise RuntimeError(f"submit got {status}: {payload}")
    return payload["job_id"]


def poll_to_terminal(base: str, job_id: str, t_submit: float,
                     interval: float = 0.5, max_wait: float = 600.0) -> dict:
    """Poll one job to a terminal state. Records when it was first seen running."""
    t_first_running = None
    deadline = time.time() + max_wait
    while time.time() < deadline:
        _, p = _get(base, f"/jobs/{job_id}")
        st = p.get("status")
        if st == "running" and t_first_running is None:
            t_first_running = time.time()
        if st in ("done", "failed", "timeout"):
            t_done = time.time()
            res = p.get("result") or {}
            summ = res.get("input_summary") or {}
            return {
                "job_id": job_id, "status": st,
                "t_submit": t_submit,
                "t_first_running": t_first_running,
                "t_done": t_done,
                "queue_wait_s": (t_first_running - t_submit) if t_first_running else None,
                "solve_s": (t_done - t_first_running) if t_first_running else None,
                "e2e_s": t_done - t_submit,
                "items_packed": summ.get("items_packed"),
                "items_unpacked": summ.get("items_unpacked"),
                "pallets_used": summ.get("pallets_used"),
                "error": p.get("error"),
            }
        time.sleep(interval)
    return {"job_id": job_id, "status": "POLL_TIMEOUT", "t_submit": t_submit,
            "t_done": time.time(), "e2e_s": time.time() - t_submit}


# ---------------------------------------------------------------- metrics

def _pct(xs, q):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = max(0, min(len(xs) - 1, int(round(q * (len(xs) - 1)))))
    return round(xs[k], 2)


def _max_overlap(records) -> int:
    """Observed max concurrency: max overlapping [first_running, done] intervals."""
    evts = []
    for r in records:
        a, b = r.get("t_first_running"), r.get("t_done")
        if a and b and b >= a:
            evts.append((a, 1)); evts.append((b, -1))
    evts.sort()
    cur = best = 0
    for _, d in evts:
        cur += d
        best = max(best, cur)
    return best


def summarize(records, wall_s: float, label: str, cfg: dict) -> dict:
    done = [r for r in records if r["status"] == "done"]
    return {
        "label": label, "config": cfg,
        "n_jobs": len(records),
        "n_done": len(done),
        "statuses": {s: sum(1 for r in records if r["status"] == s)
                     for s in sorted({r["status"] for r in records})},
        "wall_s": round(wall_s, 2),
        "throughput_jobs_per_min": round(len(done) / wall_s * 60, 2) if wall_s else None,
        "observed_max_concurrency": _max_overlap(records),
        "solve_s": {"p50": _pct([r.get("solve_s") for r in done], 0.5),
                    "p95": _pct([r.get("solve_s") for r in done], 0.95),
                    "max": _pct([r.get("solve_s") for r in done], 1.0)},
        "e2e_s": {"p50": _pct([r.get("e2e_s") for r in done], 0.5),
                  "p95": _pct([r.get("e2e_s") for r in done], 0.95),
                  "max": _pct([r.get("e2e_s") for r in done], 1.0)},
        "queue_wait_s": {"p50": _pct([r.get("queue_wait_s") for r in done], 0.5),
                         "p95": _pct([r.get("queue_wait_s") for r in done], 0.95)},
        "records": records,
    }


# ---------------------------------------------------------------- modes

def run_curve(base, box_counts, budget, max_pallets):
    out = []
    for n in box_counts:
        body = make_request(n, seed=n, budget=budget, max_pallets=max_pallets)
        t0 = time.time()
        jid = submit(base, body)
        rec = poll_to_terminal(base, jid, t0)
        rec["n_boxes"] = n
        print(f"  curve n={n:>4}  status={rec['status']:<8} "
              f"solve={rec.get('solve_s')}s  packed={rec.get('items_packed')}/"
              f"{(rec.get('items_packed') or 0)+(rec.get('items_unpacked') or 0)}  "
              f"pallets={rec.get('pallets_used')}", flush=True)
        out.append(rec)
    return out


def run_burst(base, n_jobs, n_boxes, budget, concurrency, max_pallets):
    bodies = [make_request(n_boxes, seed=1000 + i, budget=budget,
                           max_pallets=max_pallets) for i in range(n_jobs)]
    t_wall0 = time.time()
    submitted = []
    for i, b in enumerate(bodies):           # submit all up front (fills the queue)
        jid = submit(base, b)
        submitted.append((jid, time.time()))
    print(f"  submitted {n_jobs} jobs in {time.time()-t_wall0:.2f}s; draining...",
          flush=True)
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(poll_to_terminal, base, jid, ts) for jid, ts in submitted]
        records = [f.result() for f in futs]
    wall_s = time.time() - t_wall0
    return records, wall_s


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["curve", "burst"])
    ap.add_argument("--base", default="http://localhost:8000/api/v1")
    ap.add_argument("--boxes", default="60", help="curve: comma list; burst: single int")
    ap.add_argument("--jobs", type=int, default=24)
    ap.add_argument("--budget", type=float, default=90.0)
    ap.add_argument("--concurrency", type=int, default=32)
    ap.add_argument("--max-pallets", type=int, default=1)
    ap.add_argument("--label", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    if args.mode == "curve":
        counts = [int(x) for x in args.boxes.split(",")]
        print(f"[curve] budget={args.budget}s counts={counts}", flush=True)
        recs = run_curve(args.base, counts, args.budget, args.max_pallets)
        result = {"mode": "curve", "budget_s": args.budget, "records": recs}
    else:
        n_boxes = int(args.boxes)
        cfg = {"n_boxes": n_boxes, "budget_s": args.budget, "n_jobs": args.jobs}
        print(f"[burst] {args.label}  jobs={args.jobs} n_boxes={n_boxes} "
              f"budget={args.budget}s", flush=True)
        recs, wall = run_burst(args.base, args.jobs, n_boxes, args.budget,
                               args.concurrency, args.max_pallets)
        result = summarize(recs, wall, args.label or "burst", cfg)
        s = result
        print(f"  -> done={s['n_done']}/{s['n_jobs']} statuses={s['statuses']} "
              f"wall={s['wall_s']}s  throughput={s['throughput_jobs_per_min']}/min  "
              f"obs_concurrency={s['observed_max_concurrency']}  "
              f"solve_p50={s['solve_s']['p50']}s solve_p95={s['solve_s']['p95']}s  "
              f"e2e_p50={s['e2e_s']['p50']}s e2e_p95={s['e2e_s']['p95']}s", flush=True)

    if args.out:
        with open(args.out, "w") as f:
            json.dump(result, f, indent=1)
        print(f"  wrote {args.out}", flush=True)


if __name__ == "__main__":
    main()
