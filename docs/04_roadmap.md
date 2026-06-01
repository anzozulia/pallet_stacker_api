# 04 — Roadmap

The build sequence. Each phase is small, independently testable, and leaves the
project in a working state. No phase starts until its inputs (decisions, prior
phase) are green.

**Status (2026-06-01): Phases 0–6 are complete and committed; the service runs
end-to-end and is load-tested.** Phase 6 pinned the operational config (4 workers ×
OMP=2; `MAX_BOXES=500`; soft 90 / hard 120) and confirmed all 7 MVP success
criteria pass under load — see [`05_load_profile.md`](05_load_profile.md). Phase
4/7 polish also landed (per-IP rate limiting, structured logging, LICENSE,
`.env.example`). **Remaining: the rest of Phase 7 — an automated test suite
(`tests/` is still empty), optional result caching, optional queue-saturation
`503`.**

Effort markers are rough (½d = half a day) and assume the core algorithm is used
as-is.

---

## Phase 0 — Scaffolding & planning ✅ (this)
Repo, directory skeleton, `.gitignore`/`.dockerignore`, the planning docs
(`00`–`04`), and project metadata. Separate git repo, excluded from the core repo.
**Done when:** the structure + docs exist and the build sequence is agreed.

## Phase 1 — Solver adapter (`src/pallet_api/solver/`) ✅ — ~1 d
The single seam to the core. A pure function:
`solve(request_model) -> result_dict` that runs **gate → `brkga_pack_v35` →
`to_json`**, with the budget/cap/seed plumbed from config. No HTTP, no Redis yet.
- Resolve **D10** (how the core is depended on) first.
- Map the request schema ⇄ core `Box`/`Pallet`/`PackerConfig`.
- **Done when:** a unit test packs a known instance through the adapter and gets a
  valid `to_json` result; a malformed input raises the gate error.
  *(Adapter built + D10 resolved; exercised by the smoke test. The dedicated unit
  test is still outstanding — see the test-suite item under Remaining work.)*

## Phase 2 — API skeleton (`api/`, `schemas/`) ✅ — ~1 d
FastAPI app with pydantic request/response models (implementing `02_api_contract`),
plus `/health` and `/version`. `POST /pack` validates and calls the adapter
**synchronously** for now (no queue) to prove the contract end-to-end.
- **Done when:** `POST /pack` returns a valid result for a small instance and a
  `400` for malformed input; OpenAPI docs render; schema round-trips.

## Phase 3 — Async job model (`jobs/`, `workers/`) ✅ — ~1.5 d
Introduce Redis + arq. `POST /pack` enqueues and returns `202 + job_id`; a worker
process runs the solve task; `GET /jobs/{id}` reads status/result from Redis with a
TTL.
- Job states: `queued → running → done|failed`.
- **Done when:** submit → poll → result works end-to-end across separate API and
  worker processes; multiple jobs solve concurrently with N workers.

## Phase 4 — Hardening: timeouts, limits, errors ✅ — ~1 d
The operational correctness layer.
- **Hard wall-clock timeout** per job at the worker (D6) → `timeout` status.
- Box cap + per-IP rate limit (D5, D13); queue-saturation `503`.
- Robust error mapping (solver exceptions → sanitised `failed`); worker-crash →
  job-timeout/requeue.
- **Done when:** a deliberately pathological input is killed and reported as
  `timeout` without harming the worker; over-cap and rate-limited requests get the
  right status; a killed worker doesn't strand a job.

## Phase 5 — Packaging & deployment (`deploy/`) ✅ — ~1 d
`Dockerfile` (API + worker images; the worker image **builds the core's Cython
extensions**) and `docker-compose.yml` (api + redis + worker). Config via env
(D9). `Makefile` targets for dev.
- **Done when:** `docker compose up` brings up a working service; submit/poll works
  against it; restart-safe (state in Redis).

## Phase 6 — Load test & tuning ✅ — ~1 d
Validated the scaling model and pinned the operational config. Full write-up:
[`05_load_profile.md`](05_load_profile.md); raw data in `../results/loadtest/`.
- Built `scripts/loadtest.py` (stdlib harness) + `docker-compose.loadtest.yml`;
  swept `workers × OMP_NUM_THREADS` on the 10-core Docker VM.
- **Result:** solves are convergence-bounded (threads raise throughput);
  `OMP=2, workers ≈ cores/2` is the sweet spot (OMP=1 risks the big-job budget).
  Tuned D4 (shipped default **4w × OMP=2**), confirmed D5 (`MAX_BOXES=500` safe
  under contention) and D6 (soft 90 / hard 120; hard-kill verified).
- **All 7 MVP success criteria (`00_PLAN.md` §7) pass** under load — see
  `05_load_profile.md` §5.

## Phase 7 — Polish & launch prep ◑ (in progress) — ~½–1 d
README/run docs, structured logging + basic metrics, a minimal landing/usage note,
licence (open-source). Optional: result caching by input-hash (D11).
- **Done:** structured logging; MIT `LICENSE`; `.env.example`; README quick-start +
  honest status; docs refreshed to match the implementation.
- **Remaining:** an automated test suite (`tests/` is empty); basic metrics;
  optional result caching (D11).
- **Done when:** a newcomer can run it and call it from the docs alone.

---

## Out of this roadmap (separate efforts)

- **Web frontend** (3D viewer + input forms) — a separate project consuming this
  API.
- **Algorithm improvements** (e.g. closing the BR1 full-support gap) — upstream in
  the core repo, not here.
- HA Redis / multi-region / autoscaling beyond a single-box deployment.

## Critical path / dependencies

```
Phase 0 ─▶ D10 ─▶ Phase 1 ─▶ Phase 2 ─▶ Phase 3 ─▶ Phase 4 ─▶ Phase 5 ─▶ Phase 6 ─▶ Phase 7
                                              (Redis)   (hard timeout = D6)   (tune D4/D5/D6)
```

D10 (how to depend on the core) is resolved. The next step, Phase 6, has no
blocking decision — it *produces* the final calls on `MAX_BOXES` (D5) and
`workers × OMP_NUM_THREADS` (D4) from real measurements. Everything else has a
sane default recorded in `03_design_decisions.md`.
