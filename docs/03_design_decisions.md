# 03 — Design Decisions

A running log of the decisions that shape the service, with their rationale, so
future work doesn't relitigate settled questions. ADR-style: each entry is a
decision, why, and the alternatives rejected. New decisions append here.

Status legend: **ACCEPTED** (settled) · **PROPOSED** (default, may change during
build) · **OPEN** (needs a call before the relevant phase).

---

### D1 — Free, open, no-login, no-history — **ACCEPTED**
The MVP is an anonymous public tool: no auth, no accounts, no persisted history.
Results live in Redis under a TTL and then vanish. *Why:* lowest-friction
usefulness for everyone; no PII, no storage/compliance burden. *Rejected:* user
accounts / saved jobs (scope + storage + auth complexity for no MVP value).

### D2 — Asynchronous job API (submit + poll) — **ACCEPTED**
`POST /pack` returns a `job_id`; the client polls `GET /jobs/{id}`. *Why:* solves
take tens of seconds (up to ~90 s at the cap) — far too long for a synchronous
HTTP response; async keeps API instances responsive and decouples solve capacity
from request handling. *Rejected:* synchronous solve (request timeouts, blocked
workers); websockets/SSE for MVP (more moving parts than polling needs).

### D3 — Redis from day one — **ACCEPTED**
Redis is the shared queue + job-status + result store (with TTL) from the start.
*Why:* it is precisely what makes the API tier stateless and the worker tier
horizontally scalable; retrofitting it later would mean rearchitecting. *Rejected:*
in-process queue (no horizontal scaling, state lost on restart).

### D4 — Multi-worker horizontal scaling — **ACCEPTED (tuned in Phase 6)**
Capacity = number of workers; each worker runs one solve at a time, so N workers ⇒
N concurrent solves from different users. *Why:* the natural unit of work is "one
solve," and OpenMP already parallelises a single solve across cores; scaling out
processes gives clean multi-user concurrency. See `01_architecture.md` §3 for the
`workers × threads-per-worker` dial.
*Tuned (Phase 6 load test, `docs/05_load_profile.md`):* **`OMP_NUM_THREADS = 2`,
workers ≈ cores⁄2**, leaving ~2 cores for api+redis+OS when co-located. Measured on
a 10-core VM: throughput rises monotonically with worker count; OMP=1 is a false
economy (kills parallel decode, risks the big-job budget); above 2 threads/worker
trades concurrency for marginal speed. **Shipped default: 4 workers × OMP=2**
(`docker-compose.yml`). Don't let total worker-threads exceed cores.

### D5 — Input cap: 500 boxes — **ACCEPTED (confirmed in Phase 6)**
Hard-reject requests with more than 500 boxes. *Why:* the core's measured feasible
ceiling within a ~90 s budget is ~500 (it goes super-linear and overruns the
budget beyond that). Doubles as abuse protection. *Confirmed (Phase 6):* a 500-box
solve takes ~28 s uncontended (OMP=4) and ~42–50 s with **4 concurrent 500-box
solves** at OMP=2 — comfortably inside the 90 s budget, **0 timeouts**. So 500 is
safe; no need to drop to 400. The exact number stays a config value (`MAX_BOXES`,
D9). *Rejected:* the originally-discussed 5000 (the core does not complete at that
size).

### D6 — Time budget > 60 s, with a HARD worker-side timeout — **ACCEPTED**
The per-solve budget is "much bigger than 60 s" (target ~90–120 s). The solver's
own `time_limit_s` is **soft** (checked between BRKGA generations), so the worker
wraps each solve in a **hard wall-clock kill** set a bit above the soft budget.
*Why:* a single pathological input must never pin a worker indefinitely; a service
SLA needs a real ceiling. *This is the one item the core deliberately deferred to
the service layer.* *Confirmed (Phase 6):* **soft 90 / hard 120** keeps ample
margin (worst case observed ~50 s); a forced `HARD=10` killed a 500-box solve at
10.15 s → `timeout`, and the next job ran `done` — the worker survives the kill.
*Rejected:* relying on the solver's soft budget alone (it can overrun 100–300 % at
scale).

### D7 — Integer-only spatial input contract — **ACCEPTED**
Box and pallet **dimensions** (and overhang) must be positive integers; weights
may be fractional. Enforced at the boundary by the core's input gate; violations
are rejected with a `400`, never silently rounded. *Why:* the engine works on an
integer grid — non-integer dims produce real geometric overlaps (verified). Units
are caller-defined (mm/cm/inch) and must be consistent. *Rejected:* accept floats
and round (produces invalid packings).

### D8 — Stack: FastAPI + arq + Redis + pydantic — **ACCEPTED (implemented)**
FastAPI/uvicorn for the async HTTP API; **arq** for the Redis-backed async job
queue + worker loop (async-native, matches FastAPI; lighter than Celery, async
unlike RQ); pydantic for schema validation and the JSON⇄core-object mapping.
*Revisit only if* a concrete need (e.g. complex routing, scheduled tasks) outgrows
arq. *Rejected for MVP:* Celery (heavyweight), RQ (sync), raw Redis lists
(reinventing the worker loop + timeouts + retries).

### D9 — Configuration via environment — **ACCEPTED (implemented)**
All operational knobs are environment-driven (12-factor): `REDIS_URL`,
`MAX_BOXES`, `SOFT_BUDGET_S`, `HARD_BUDGET_S`, `DEFAULT_MAX_PALLETS`,
`OMP_NUM_THREADS` (per worker), `RESULT_TTL_S`, `RATE_LIMIT_*`, `DEFAULT_SEED`.
*Why:* tune per deployment without code changes; the worker/thread split and caps
are exactly the things that vary by host.

### D10 — Core algorithm is VENDORED — **ACCEPTED (revised)**
The worker imports `pallet_packer`, reached through the single `solver/` adapter
import point. **The core is vendored into this repo under `core/`** (source only —
`.py` + Cython `.pyx`/`.pxd` + build files; see `core/VENDOR.md`). The Docker image
builds it in a builder stage (`pip wheel ./core`, compiling the 6 Cython/OpenMP
extensions) and installs the wheel into a slim runtime; `pallet_packer` is then a
normal installed package (no host mount, no `PYTHONPATH=/core`).
*Why:* this service is a **standalone, independent project** — anyone who clones it
gets a working `docker compose up --build` with no external repo, no sibling-dir
mount, and no pip-from-git of a separate (possibly private) repo. Provenance +
refresh instructions live in `core/VENDOR.md`.
*Supersedes* the earlier plan (pip-install-from-git in prod + a read-only `/core`
dev mount), which required the core to be published and a specific two-repo layout.
*Trade-off accepted:* the vendored copy can drift from upstream — refresh it
deliberately (re-copy + bump the commit in `core/VENDOR.md`). The adapter is the
only module that imports the core, so the engine stays swappable.

### D11 — Determinism / seed handling — **ACCEPTED (seed); caching OPEN**
Default to a fixed service seed so identical input yields an identical plan
(reproducible, debuggable). Allow an optional caller-supplied `seed`. *Why:* the
core is bit-identical at a fixed seed and thread-invariant; exposing it is free and
useful. *Implemented:* `options.seed` falls back to `DEFAULT_SEED` (adapter). *Open
sub-question (not done):* whether to also cache results by input-hash to dedup
identical resubmissions (a possible optimisation, not MVP).

### D12 — Honest quality expectations — **ACCEPTED (informational)**
The packings are physically valid but not world-class density: ~1.7 pp behind 2013
full-support academic SOTA (competitive on heterogeneous loads, weaker on
homogeneous). *Why it matters here:* set user expectations honestly in any
service-facing copy; do not market it as state-of-the-art. Fine for a free general
tool. See the core repo's `docs/reports/31`.

### D13 — Per-IP rate limiting — **ACCEPTED (implemented)**
Basic per-IP rate limit on `POST /pack` plus the box cap. *Why:* abuse protection
without accounts. *Implemented:* a fixed-window per-minute counter in Redis
(`api/limits.py`), limit from `RATE_LIMIT_PER_MIN` (D9); over-limit → `429`.
*Known follow-up:* the client IP is taken from `request.client` only — behind a
reverse proxy/LB it should honour `X-Forwarded-For` (a `trust_proxy` switch), else
all forwarded clients share one bucket. *Rejected for MVP:* API keys / quotas
(implies accounts); token-bucket (fixed-window is sufficient here).

---

## Decisions still open

The operational tuning is now settled by the **Phase 6 load test**
(`docs/05_load_profile.md`): `MAX_BOXES = 500` (validated within budget under
contention), budgets **soft 90 / hard 120**, and **`OMP_NUM_THREADS = 2` with
workers ≈ cores⁄2** (shipped default 4 × 2). D10 is resolved (core mounted at
`/core` in dev, pinned `pip install` in prod); the v1 constraint surface is weight,
fragility, support, group, `max_pallets`, overhang, with CoG at defaults.

What remains are optional / follow-up items only:

| Open item | Resolution | Status |
|---|---|---|
| Result caching by input-hash (D11) | optional, post-MVP | not implemented |
| `X-Forwarded-For` / `trust_proxy` for rate limiting (D13) | follow-up hardening | uses `request.client` only |
| Queue-saturation `503` (shed load at max queue depth) | follow-up hardening | not implemented (queue just grows e2e latency) |
| Automated test suite | pre-launch | `tests/` still empty |
