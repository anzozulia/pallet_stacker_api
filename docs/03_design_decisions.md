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

### D4 — Multi-worker horizontal scaling — **ACCEPTED**
Capacity = number of workers; each worker runs one solve at a time, so N workers ⇒
N concurrent solves from different users. *Why:* the natural unit of work is "one
solve," and OpenMP already parallelises a single solve across cores; scaling out
processes gives clean multi-user concurrency. See `01_architecture.md` §3 for the
`workers × threads-per-worker` dial.

### D5 — Input cap: 500 boxes — **ACCEPTED**
Hard-reject requests with more than 500 boxes. *Why:* the core's measured feasible
ceiling within a ~90 s budget is ~500 (it goes super-linear and overruns the
budget beyond that). Doubles as abuse protection. *Note:* a slightly lower cap
(e.g. 400) buys safety margin for worst-case loads; the exact number is a config
value (`MAX_BOXES`) — see D9. *Rejected:* the originally-discussed 5000 (the core
does not complete at that size).

### D6 — Time budget > 60 s, with a HARD worker-side timeout — **ACCEPTED**
The per-solve budget is "much bigger than 60 s" (target ~90–120 s). The solver's
own `time_limit_s` is **soft** (checked between BRKGA generations), so the worker
wraps each solve in a **hard wall-clock kill** set a bit above the soft budget.
*Why:* a single pathological input must never pin a worker indefinitely; a service
SLA needs a real ceiling. *This is the one item the core deliberately deferred to
the service layer.* *Rejected:* relying on the solver's soft budget alone (it can
overrun 100–300 % at scale).

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

### D10 — Core algorithm as a pinned dependency — **ACCEPTED**
The worker imports `pallet_packer`. Resolution: **the core is an installed
package, never vendored or modified here**, reached through the single `solver/`
adapter import point. Two acquisition modes:
- **Production / CI:** `pip install` the core from its git repo at a **pinned
  commit/tag**; the service Docker image **builds the Cython extensions** in the
  image (toolchain + `numpy`/`cython` build deps, exactly like the core's own
  Dockerfile). Reproducible, explicit version, clean separation.
- **Local dev:** the core repo is **mounted read-only** into the containers at
  `/core` with `PYTHONPATH=/core`, reusing its already-built platform `.so`. Zero
  rebuild, instant iteration. This is what `docker-compose.yml` does today.
*Rejected:* git submodule (tighter coupling than needed) and vendoring (drifts
from upstream). The `solver/` adapter is the only module that imports the core, so
swapping dev↔prod acquisition touches nothing else.

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

Most build-time decisions are now settled (D10 resolved → core mounted at `/core`
in dev, pinned `pip install` in prod; the v1 constraint surface is weight,
fragility, support, group, `max_pallets`, overhang, with CoG at defaults). What
remains is the operational tuning that **Phase 6 (load test) produces**, plus one
optional feature:

| Open item | Resolved by | Current value (default in use) |
|---|---|---|
| Final `MAX_BOXES` (400 vs 500) | Phase 6 load test | 500 |
| Final `SOFT`/`HARD` budgets | Phase 6 load test | soft 90 s / hard 120 s |
| `OMP_NUM_THREADS` × worker count | Phase 6 load test | 2 threads × 2 workers (compose default) |
| Result caching by input-hash (D11) | optional, post-MVP | not implemented |
| `X-Forwarded-For` / `trust_proxy` for rate limiting (D13) | follow-up hardening | uses `request.client` only |
