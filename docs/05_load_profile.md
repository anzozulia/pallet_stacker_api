# 05 — Load Profile & Recommended Deployment Config (Phase 6)

The empirical scaling study that pins the operational config. It answers: how does
a solve cost scale, how should `workers × OMP_NUM_THREADS` be split, does the
500-box cap stay within budget under load, and does the hard timeout work. All
numbers come from `scripts/loadtest.py` against the `docker compose` stack; raw
results are in [`../results/loadtest/`](../results/loadtest/).

> **Read these as relative guidance, not absolute SLAs.** They were measured on a
> single laptop Docker VM (below). The *shape* of the tradeoff transfers; the
> absolute throughput/latency will differ on production hardware. Re-run the
> harness on the target host to re-pin.

## Test environment

| | |
|---|---|
| Host | Apple Silicon, 14 logical cores, 24 GB |
| Docker VM | **10 CPUs**, ~11.7 GB (this is the worker pool's compute envelope) |
| Stack | `docker-compose.loadtest.yml` — redis + api + N workers; core mounted at `/core` (dev mode) |
| Workload | heterogeneous integer-dim boxes (12-SKU catalogue, ~17% fragile), pallet 1200×1000×1500, `max_pallets=1`, `seed=42` |
| Budgets | soft `time_budget_s=90`, hard `120` (unless noted) |
| Date | 2026-06-01 |

Method: **`curve`** submits one solve at a time (single-solve cost); **`burst`**
submits 24 jobs at once and drains them concurrently (throughput, latency,
observed concurrency). "Observed concurrency" is the max overlap of
`[first-seen-running, done]` intervals — it carries a **+1 boundary artifact**
from poll timing at job handoff, so real concurrency = worker count.

## 1. Single-solve cost curve — OMP=4, 1 worker, no contention

| boxes | 10 | 50 | 100 | 200 | 350 | 500 |
|---|---|---|---|---|---|---|
| solve (s) | 5.1 | 8.1 | 13.2 | 17.7 | 30.4 | 28.4 |

Three findings that drive everything below:

1. **Solves are convergence-bounded, not time-bounded.** Every solve finished far
   under the 90s soft budget (max 30s). The solver hits its patience/convergence
   and returns early. ⇒ adding threads raises **throughput** (a solve finishes
   sooner and frees the worker), not just solution quality.
2. **A 500-box worst case is only ~28s** at OMP=4 — well inside budget. (Solve time
   is not strictly monotonic in box count; it depends on per-instance convergence.)
3. **~5s floor** even at 10 boxes. That is the per-solve cost of spawning a fresh
   subprocess and re-importing the core — the price of the hard-timeout isolation
   (a runaway solve must be killable, which a thread is not). See §7.

## 2. `workers × OMP_NUM_THREADS` sweep — 10-core VM, 24 × 60-box jobs

| config | total threads | throughput (jobs/min) | solve p50 | solve p95 | e2e p50 | e2e p95 | concurrency |
|---|---|---|---|---|---|---|---|
| 1w × 8t | 8 | 6.5 | 9.1 | 10.1 | 120.8 | 212.8 | ~1 |
| 2w × 4t | 8 | 11.9 | 10.1 | 11.6 | 70.4 | 118.9 | ~2 |
| **4w × 2t** | 8 | **18.6** | 12.6 | 14.7 | 47.0 | 73.8 | ~4 |
| 5w × 2t | 10 | 22.4 | 12.6 | 14.7 | 39.0 | 62.7 | ~5 |
| 8w × 1t | 8 | 26.8 | 17.2 | 21.3 | 34.0 | 53.1 | ~8 |

(`e2e` = submit→done, including queue wait; these are **burst/saturation** numbers —
worst-case queueing with 24 jobs arriving at once.)

- **Throughput rises with worker count**, monotonically, even though each solve gets
  slower — more concurrent solves more than offset the per-solve slowdown.
- **OMP=1 is a false economy.** `8w×1t` wins raw small-job throughput, but OMP=1
  disables parallel decoding: solve p50 jumps to 17s and p95 to 21s, and a *500-box*
  solve at OMP=1 would be roughly 4× the OMP=4 baseline (~28s → ~110s) — that
  **blows the 90s budget and risks a timeout** on big jobs (criterion #4). Not worth it.
- **OMP=2 is the floor for useful parallelism** and keeps big jobs safe. Above 2
  threads/worker, you trade away concurrency for marginal per-solve speed — the wrong
  trade for a free multi-user tool.
- **Don't oversubscribe.** Total worker threads should not exceed VM cores; beyond
  that, per-solve time inflates sharply from contention (seen in earlier 16×-inflated
  contended runs).

## 3. Worst-case validation — 500-box under full contention (criterion #4)

`4w × 2t`, **8 concurrent 500-box jobs** (2× the workers, so 4 cap-size solves run
at once at OMP=2): **8/8 `done`, 0 timeouts**, solve **p50 41.6s / p95 49.8s**,
e2e p95 88s. ⇒ the 500-box cap **stays within the 90s budget even when every worker
is chewing a cap-size job at once**. Over-cap (501 boxes) → **HTTP 400**
(`too many boxes: 501 exceeds the limit of 500`).

## 4. Hard timeout (criterion #6)

With `HARD=10`, a 500-box solve (which wants ~45s) was **hard-killed at 10.15s →
`timeout`**; the **next 20-box job completed (`done`, 5.07s)**. The kill fires on
schedule and the worker survives to serve the next job.

## 5. MVP success criteria (`00_PLAN.md` §7) — all PASS

| # | Criterion | Evidence |
|---|---|---|
| 1 | POST → `job_id` | every burst: 24/24 submitted `202` in <0.1s |
| 2 | poll → `done` with valid result | every job reached `done`; results carry the full `to_json` packing |
| 3 | malformed → `400` | smoke (`length 100.5`) + over-cap 501 → `400 invalid_input` |
| 4 | 500-box within budget; over-cap rejected | §3: 4 concurrent 500-box solves ≤ ~50s < 90s; 501 → `400` |
| 5 | N concurrent solves | §2: observed concurrency tracks worker count |
| 6 | hard timeout → `timeout`, worker survives | §4: killed at 10.15s, next job `done` |
| 7 | runs from `docker compose up` | the entire study ran on the compose stack |

## 6. Recommended deployment config

**Rule of thumb:** `OMP_NUM_THREADS = 2`; **workers ≈ cores ⁄ 2**; leave ~2 cores of
headroom for the API + Redis + OS when co-locating them. This finalises **D4**.

| Deployment | Config | Expected (this VM) |
|---|---|---|
| **Single box (shipped compose), 8–10 cores** | **4 workers × OMP=2** | ~18.6 jobs/min, e2e p95 ~74s under saturation, 500-box safe, 2 cores headroom |
| Dedicated worker host (API/Redis elsewhere) | workers = cores⁄2 (e.g. 5 × 2 on 10 cores) | ~22 jobs/min (+20%), full-core utilisation |
| Low-traffic / big-job latency priority | 2 workers × 4 threads | faster single solve, lower concurrency |

- **`MAX_BOXES = 500`** (D5) — validated to complete within the 90s budget even under
  4-way contention. No need to lower it to 400.
- **Budgets soft 90 / hard 120** (D6) — ample margin; the worst case observed was
  ~50s. Keep 90/120 to absorb harder-converging instances; the hard cap catches
  genuine runaways.

The shipped `docker-compose.yml` default is set to **4 workers × OMP=2** per this
study. Reduce the worker count on smaller hosts (keep total worker-threads ≤ cores).

## 7. Notes & future work

- **~5s spawn/import floor per solve.** Every solve forks a fresh process and
  re-imports the core (required for the killable hard timeout). For a workload of
  many tiny jobs, a warm persistent solve-process pool (with its own kill protocol)
  could cut the floor — a post-MVP optimisation, not worth the complexity now.
- **No queue-saturation `503` yet.** With a bounded worker pool the queue simply
  grows end-to-end latency (measured: `1w×8t`, 24 queued jobs → e2e p95 213s). For
  production, a max-queue-depth `503` (D-follow-up) would shed load gracefully.
- **Reproduce:** `LT_OMP=2 docker compose -f docker-compose.loadtest.yml up -d --build --scale worker=4`,
  then `python3 scripts/loadtest.py burst --jobs 24 --boxes 60 --budget 90 --label 4wx2t`.
