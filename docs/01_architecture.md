# 01 — Architecture

How the service is shaped, how a request flows through it, how it scales, and how
it fails safely.

---

## 1. Components

```
                         ┌──────────────────────────────┐
   client ──HTTP──▶      │   API instances (stateless)  │   FastAPI + uvicorn
   (POST /pack,          │   - validate input (gate)    │   (scale: many, cheap)
    GET /jobs/{id})      │   - enqueue job              │
                         │   - read job/result          │
                         └───────────────┬──────────────┘
                                         │
                                   enqueue │ read
                                         ▼
                         ┌──────────────────────────────┐
                         │            Redis              │   single source of truth
                         │  - job queue                  │
                         │  - job status hash            │
                         │  - result store (TTL)         │
                         └───────────────┬──────────────┘
                                         │ pull job / write result
                                         ▼
                         ┌──────────────────────────────┐
                         │      worker pool (arq)        │   each worker = 1 solve
                         │  - pull one job               │   at a time
                         │  - hard wall-clock timeout    │   (scale: N workers =
                         │  - run pallet_packer solve    │    N concurrent solves)
                         │  - write result + status      │
                         └──────────────────────────────┘
```

- **API instances** — stateless FastAPI apps. They never solve; they validate,
  enqueue, and read back. Any instance can serve any request. Scale horizontally
  behind a load balancer.
- **Redis** — the only shared state: the job queue, per-job status, and the
  result store (with a TTL, since there is no persisted history). Present from
  day one (it is what makes the API stateless and the workers scalable).
- **Workers** — each pulls one job and runs exactly one solve at a time, calling
  the `pallet_packer` core. The number of workers is the concurrency ceiling:
  N workers ⇒ N simultaneous solves from different clients.

## 2. Request / job lifecycle

```
1. POST /pack {boxes, pallet, options}
2. API: validate via the core input gate (integer dims, ≤500 boxes, …)
        → invalid  ⇒ 400 with problems[]   (nothing queued)
        → valid    ⇒ create job_id, store status=queued, enqueue payload
        ⇒ 202 {job_id, status: "queued"}
3. Worker: pull job → status=running → solve under a HARD wall-clock cap
        → success  ⇒ store result (to_json), status=done, set TTL
        → timeout  ⇒ status=timeout
        → error    ⇒ status=failed, store reason
4. GET /jobs/{job_id}
        → {status, result?}   (result present iff status=done)
```

The solver's own `time_limit_s` is **soft** (checked between BRKGA generations).
The worker therefore wraps the solve in a **hard** wall-clock kill so a single
pathological input cannot pin a worker indefinitely. The hard cap is set somewhat
above the soft budget (e.g. soft 90 s, hard 120 s) to allow clean finish first.

## 3. Scaling model

The concurrency dial is **`workers × threads-per-worker`** and it is *deployment
config*, not code — the solver parallelises with OpenMP, controlled by
`OMP_NUM_THREADS` per worker process.

| Config (per 8-core box) | Concurrent solves | Per-solve speed | Fit |
|---|---|---|---|
| 1 worker × 8 threads | 1 | fastest | low traffic, big jobs |
| 2 workers × 4 threads | 2 | ~3.5× | balanced |
| **4 workers × 2 threads** | **4** | ~1.8× | **a free multi-user tool — favours throughput + fairness** |

For a free public tool we lean toward **more workers, fewer threads each** — serve
several users at once rather than make one job finish marginally faster. Below
~2 threads the parallel benefit mostly vanishes, so 2–4 threads/worker is the
sweet spot. This is tunable per deployment without code changes.

> **Measured (Phase 6, `05_load_profile.md`).** The table above is borne out
> empirically on a 10-core box: throughput rises monotonically with worker count,
> OMP=1 is a false economy (slow solves + big-job budget risk), and **`OMP=2` with
> workers ≈ cores⁄2** is the pick. Shipped default: **4 workers × OMP=2**.

- **API tier** scales independently (it does no heavy work).
- **Worker tier** scales to match demand; capacity = number of workers.
- **Redis** is a single logical instance for the MVP (HA/cluster is a later
  concern; the result store is ephemeral, so durability requirements are low).

## 4. Failure modes & safety

| Failure | Handling |
|---|---|
| Malformed input | Rejected at submit (`400`), never queued |
| Input over the cap | Rejected at submit (`413`/`400`) |
| Solve exceeds budget | Hard wall-clock kill at the worker ⇒ `timeout` status; worker survives |
| Worker crash mid-solve | Job's running-lock/visibility times out ⇒ requeued or marked `failed` (arq job timeout + retry policy) |
| Solver raises | Caught ⇒ `failed` with a sanitised reason |
| Redis eviction / result TTL expiry | `GET` on an expired job returns `404`/`expired` (no history by design) |
| Queue backlog | Bounded queue + clear "busy/retry-later" signal; never unbounded memory growth |
| Duplicate submit | Each submit is its own job (no dedup in MVP); idempotency keys are a later option |

## 5. Determinism & reproducibility

The core is bit-identical at a fixed seed and thread-invariant. The service uses a
fixed default seed so the same input yields the same plan; whether to expose a
caller-supplied seed is an API-contract decision (see `02_api_contract.md`).

## 6. What lives where (package layout)

```
src/pallet_api/
  api/        FastAPI routers: /pack, /jobs/{id}, /health, /version
  schemas/    pydantic request/response models <-> core Box/Pallet/Config/Result
  jobs/       job model, id generation, enqueue, status + result read/write (Redis)
  workers/    arq worker definition + the solve task (with the hard timeout)
  solver/     thin adapter: request -> core gate -> brkga_pack_v35 -> to_json
  config/     settings (Redis URL, budgets, caps, worker/thread counts, limits)
```

The **`solver/` adapter is the only place that imports `pallet_packer`**. Keeping
that boundary thin and single-point makes the core dependency easy to pin, mock in
tests, and reason about.

## 7. Tech choices (rationale in `03_design_decisions.md`)

- **FastAPI + uvicorn** — async HTTP, automatic OpenAPI docs, pydantic-native.
- **arq** — async-native Redis job queue (matches FastAPI's async model;
  lighter than Celery, async unlike RQ). Provides job timeouts, retries, and the
  worker loop.
- **Redis** — queue + job/result store with TTL.
- **pydantic** — request/response validation and the JSON ⇄ core-object mapping.
