# 00 — Master Plan

The single source of truth for *what* we are building and *why*. Architecture,
the API contract, decisions, and the build sequence each have their own doc; this
one frames the whole thing.

---

## 1. Vision

A **free, open, no-login web service** that solves 3D pallet/container loading:
give it a list of boxes and a container, get back a physically valid packing plan.
It exposes the independently-verified `pallet_packer` BRKGA algorithm over a
simple HTTP API so that anyone — a hobbyist, a small logistics operator, another
tool — can use it without installing anything or signing up.

The service is the **API**. A web frontend (3D viewer, input forms) is a separate,
later project that consumes this API.

## 2. Why a service (and why async)

- The algorithm is a Python/Cython package; most consumers can't or won't run it
  locally. An HTTP API makes it universally usable.
- A real solve takes **tens of seconds** (up to ~90s at the 500-box cap). That is
  far too long for a synchronous request/response. So the API is **asynchronous**:
  submit a job, get a `job_id`, poll for the result. This keeps API instances
  responsive and lets solving scale independently of request handling.

## 3. Scope

### In scope (MVP)
- `POST /pack` — validate input, enqueue a solve job, return `202 + job_id`.
- `GET /jobs/{job_id}` — poll job status; return the packing result when done.
- `GET /health`, `GET /version` — operational endpoints.
- Input validation at the boundary (the core's input gate: integer dims,
  positivity, finiteness, ≤500 boxes, unique ids).
- Async job execution on a horizontally-scalable worker pool, backed by Redis.
- Hard per-job wall-clock timeout (enforced at the worker).
- Per-IP rate limiting (basic abuse protection).
- Containerised deployment (API + Redis + workers).

### Out of scope (MVP — explicitly not building now)
- Authentication / accounts / API keys.
- Persisted history of past jobs (results are ephemeral, TTL-expired in Redis).
- Billing / quotas beyond simple rate limits.
- The web frontend (separate project).
- Re-implementing or modifying any packing logic (we consume the core as-is).

## 4. Who uses it

Anonymous, open to everyone. No identity, no session. A request carries only its
input; a job is referenced only by an opaque `job_id`.

## 5. The thing it wraps

The `pallet_packer` core (separate repo). Established facts we build on (from the
core's verification reports `30`–`32`):

| Property | Value |
|---|---|
| Input contract | **Integer** spatial dimensions (any unit, consistent); float weights OK |
| Feasible size | **~500 boxes** within a ~90s budget; degrades beyond |
| Correctness | Validator-clean for real-world integer input; all constraints honored |
| Determinism | Bit-identical at a fixed seed; thread-invariant |
| Constraints | weight cap, fragility (max-load-on-top), support ratio, centroid, CoG envelope, overhang, **group co-location**, max pallets |
| Output | Full JSON via `to_json()`: per-item placement + orientation + support graph, per-pallet utilisation + CoG, unpacked list |
| Honest quality | Physically valid; ~1.7 pp behind 2013 full-support SOTA on academic BR (competitive on heterogeneous loads, weaker on homogeneous) — fine for a free general tool |

The service **must not** weaken these guarantees. In particular it enforces the
integer-input contract and the size cap at the boundary, and it surfaces the
core's validator result.

## 6. Non-functional requirements

- **Stateless API instances** — any instance can serve any request; all shared
  state lives in Redis. Enables trivial horizontal scaling of the API tier.
- **Horizontal worker scaling** — N workers ⇒ N concurrent solves. The unit of
  capacity is "one solve."
- **Hard time bounds** — every job has a wall-clock ceiling enforced by the
  worker, because the solver's internal budget is *soft* (a runaway solve must
  not pin a worker forever).
- **Determinism / reproducibility** — same input + same seed ⇒ same result.
- **Bounded inputs** — the input gate rejects anything outside the contract with
  a clear `4xx` before any work is queued.
- **Observability** — structured logs + health/metrics, so the worker pool can be
  sized and watched.

## 7. Success criteria (MVP "done")

1. A client can POST a well-formed packing request and receive a `job_id`.
2. Polling that `job_id` returns `queued` → `running` → `done` with a complete,
   validator-clean packing result (or `failed`/`timeout` with a clear reason).
3. Malformed input is rejected at submit time with an actionable `400`.
4. A 500-box worst-case load completes within the configured budget and returns a
   valid result; a load that exceeds the cap is rejected.
5. Multiple jobs from different clients solve concurrently (N workers).
6. A job that exceeds its hard wall-clock limit is killed and reported as
   `timeout` without taking down the worker.
7. The whole thing runs from `docker compose up` (api + redis + workers).

## 8. Relationship to the core repo

- The core (`pallet_packer`) is a **dependency**, pulled in by the worker image.
  How exactly (pip-from-git, pinned version, or vendored) is decided in
  [`03_design_decisions.md`](03_design_decisions.md).
- This repo owns **only** the HTTP/async/scaling/ops layer. Any change needed to
  the packing behaviour is made upstream in the core repo, not here.

## 9. Reading order

[`01_architecture.md`](01_architecture.md) → [`02_api_contract.md`](02_api_contract.md)
→ [`03_design_decisions.md`](03_design_decisions.md) → [`04_roadmap.md`](04_roadmap.md).
