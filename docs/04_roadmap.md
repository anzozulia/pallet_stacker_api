# 04 — Roadmap

The build sequence. Each phase is small, independently testable, and leaves the
project in a working state. No phase starts until its inputs (decisions, prior
phase) are green.

**Status (2026-06-01): Phases 0–5 are complete and committed; the service runs
end-to-end (submit → poll → done, plus 400/429 and the hard timeout, verified
live).** Phase 4/7 polish landed too (per-IP rate limiting, structured logging,
LICENSE, `.env.example`). **Remaining: Phase 6 (load test + tuning) and the rest
of Phase 7 (this docs refresh; optional result caching). A note on Phase 1's
acceptance: the adapter is built and exercised by `scripts/smoke_e2e.sh`, but the
unit-test suite it called for does not exist yet — `tests/` is still empty.**

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

## Phase 6 — Load test & tuning ⏳ (next) — ~1 d
Validate the scaling model and pin the operational config.
- Drive concurrent submissions at the cap; measure throughput, latency, queue
  depth, worker saturation.
- Tune `workers × OMP_NUM_THREADS` (D4) and finalise `MAX_BOXES` (D5) and the
  budgets (D6) from real numbers.
- **Done when:** a documented load profile + a recommended deployment config exist;
  the MVP success criteria (`00_PLAN.md` §7) all pass.

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
