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
*Amended (hardening plan F2, 2026-07-02):* the Phase 6 confirmation used a MIXED
catalogue; a HOMOGENEOUS 500-box load with any finite constraint drove the v2
warm-start unbounded (~3 min, always hard-killed). Fixed by giving the v2 seed a
hard deadline of half the solve budget (`PalletPacker.pack(time_limit_s=...)`,
partial packs still seed the BRKGA); the 500 cap now holds for homogeneous
constrained loads too. See `docs/06_hardening_plan.md` Phase B.

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
useful. *Implemented:* `options.seed` falls back to `DEFAULT_SEED` (adapter); the
schema rejects negative seeds (`ge=0` — numpy's RNG raises on them, and that must
be a `422`, not a `solver_error`). *Expectation to set (F14):* on highly symmetric
loads (e.g. all-identical boxes) different seeds can legitimately return identical
plans — every flat dense layout ties on volume AND realism; this is determinism
working, not the seed being ignored. *Open sub-question (not done):* whether to
also cache results by input-hash to dedup identical resubmissions (a possible
optimisation, not MVP).

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
*Amended (hardening plan C8, 2026-07-02):* a Redis error inside the limiter used
to escape as a bare `500` outside the error envelope; it now fails **closed** with
the same `503 degraded` envelope as `/pack`'s own queue path (Redis being down
means the queue is down anyway). *Known follow-up:* the client IP is taken from
`request.client` only — behind a reverse proxy/LB it should honour
`X-Forwarded-For` (a `trust_proxy` switch), else all forwarded clients share one
bucket. *Rejected for MVP:* API keys / quotas (implies accounts); token-bucket
(fixed-window is sufficient here).

### D14 — Realism layer: recenter + orientation alignment + realism fitness — **ACCEPTED (implemented)**
A 19-scenario live study showed the solver's layouts were constraint-correct but
"real-world wrong": loads jammed into the (0,0) corner (CoG up to 67% off-centre
— a forklift hazard), identical cartons randomly rotated within a layer, heavy
boxes stacked on light ones, and towers next to bare deck. Root cause: fitness is
pure volume, which TIES for every feasible layout once all boxes fit, so seeds,
budgets, evolution, and local search were observational no-ops and layout quality
was decided entirely by corner-seeking decoder tie-breaks. *Implemented (all
default OFF in the core, ON in the service; independent env kill-switches, D9):*
(a) `postprocess.py` — a rigid per-pallet recenter (weighted CoG to deck centre,
never touches z, never creates overhang) and a feasibility-preserving same-SKU
orientation re-alignment per z-level, both replay-validated with full revert;
(b) an epsilon-scaled secondary fitness term (weights 0.5/0.4/0.1: height
moment + max height + height-class consistency — the orientation term is keyed
on the vertical extent, since yaw variation within a layer is legitimate
interlocking while a tipped box breaks the layer top) threaded through every
fitness site — batch, polish phases, SKU-aware, v2-hybrid, and the v2
candidate tie-break — with a bounded-loss guarantee: it can never cost more
than half the smallest box's volume, so it never drops a box; (c) a gated
heavy-first seed chromosome making `heavy_on_bottom` real in the v3.5 path.
*Known residual (`make realism-check`):* in dense mixed layers a single
straggler box can still be tipped by the greedy per-placement rotation argmax
when no flat placement fits its gap — flagged TIPPED_IN_LAYER by the battery
(3/19 scenarios); the fix is the deferred decoder-level rotation tie-break.
*Consequence:* `options.seed` and `time_budget_s` now have observable effect
(the fitness landscape has a gradient). *Rejected:* enabling the CoG-envelope
constraint (rejects placements, can't centre a layout, misfires on
unlimited-weight pallets); per-box centering in the decoders (corner anchoring
is what makes EMS packing dense). *Deferred:* decoder-level rotation tie-breaks
and brick-bond interlock (Cython twin fan-out + BR re-validation risk).
*Amended (hardening plan A/C, 2026-07-02, after an adversarial evaluation —
see `docs/06_hardening_plan.md`):* (1) the align pass now SKIPS any box that
supports another entirely — the old gate only blocked dz changes, and a yaw
swap could pull the footprint out from under a dependent; (2) the post-pass
safety net `validate()` was hardened to the engine's feasibility stack
(per-box `requires_full_support`, centroid-over-supporter, no zero-contact
floaters, explicit CoG ranges; load bearing stays DIRECT-supporter on purpose
— the BRKGA decoders' commit model is direct-only, F15); (3) `realism_weight`
is clamped to [0,1] so the bounded-loss guarantee cannot be voided; (4)
recentring is skipped when the caller sets explicit `cog_x_range`/`cog_y_range`
(the envelope owner decides placement; the centred fractional envelope needs no
guard — a shift toward centre can only move the CoG deeper into it); (5) the
align time-box is one GLOBAL 4 s budget shared across pallets instead of
2 s/pallet; (6) duplicate box ids disable the realism term (the scalar path is
id-keyed, the batch path positional — a desync would corrupt the search).

### D15 — Boundary hardening: schema bounds + body cap + sentinel guard — **ACCEPTED (implemented)**
An adversarial evaluation (`docs/06_hardening_plan.md`) showed the boundary
accepted well-typed but hostile values: 100 KB box ids round-tripped the whole
pipeline (a 500-box × 100 KB-id body is ~50 MB with several-hundred-MB transient
RAM amplification), `weight: Infinity` passed `ge=0`, `max_overhang: 5000` let a
box sit fully off the deck while staying contract-"valid", and box weights
≥ 1e18 aliased the decoders' finite `_NO_LIMIT` sentinel — silently unpackable
even on an unlimited pallet. *Implemented:* (a) schema bounds — `id`/`group`
≤ 128 chars, weight-like fields ≤ 1e12 with `allow_inf_nan=False`,
`max_overhang ≤ min(pallet.length, pallet.width)`, `max_pallets ≤ 100`; (b) a
pure-ASGI **request-body cap** (`api/bodylimit.py`, `MAX_BODY_BYTES`, default
10 MB) returning the enveloped `413 payload_too_large` — an honest
`Content-Length` is rejected before the body is read, a missing/lying header is
caught by counting received bytes (the counting path must raise starlette's
`HTTPException`, which FastAPI re-raises untouched, where a custom exception
would be remapped to a bare 400); (c) the CORE gate rejects `weight ≥ 1e15`
(500 × 1e15 stays under the 1e18 sentinel) so un-schema'd library callers are
covered too; (d) `group: ""`/whitespace normalises to *no group* in the adapter
(it previously co-located every empty-group box onto ONE pallet). *Rejected:*
raising the id cap (nothing legitimate needs >128); bounding overhang at the
schema only (library callers would still hit the sentinel).
*Amended (round 2, 2026-07-03):* spatial dims are now bounded at **1e6**
(schema `le` + core gate `MAX_DIM`) — unbounded dims silently wrapped the
core's int64 hot-path products: garbage support/load checks from ~3e9 (invalid
plans served `done`), corrupt batch fitness from ~2.1e6, raw `OverflowError`
at 2⁶³. With dims ≤ 1e6 every area/volume/sum provably fits int64. 1e6 units
≈ 1 km in mm; pick a smaller unit if exceeded.

### D16 — Physical load model: transitive load bearing + floor deck rule — **ACCEPTED (implemented)**
Round-2 adversarial evaluation (docs/07_hardening_round2.md) showed two
physical holes shared by BOTH engines: (1) load bearing was enforced against
DIRECT supporters only — a 10-stack of individually-legal links left the
bottom box carrying **8.6×** its `max_load_on_top` (F19); the v2 engine's
"transitive" commit never rejected fresh columns either, because its check
was direct-only; (2) floor placements were unconditionally "supported", so
with overhang active boxes were placed **fully off the deck, floating in
air** (F17; live probe: 6/12 boxes at 0% deck contact). *Implemented:*
(a) `PackerConfig.transitive_load_bearing` (default OFF = historical; the
service enables via `PALLET_API_TRANSITIVE_LOAD`, default 1): the BRKGA JIT
commit gains a transitive sibling AND a transitive dry-run **check** (the
check must walk the chain — check-direct/commit-transitive alone never cuts a
fresh column), mirrored in the v2 engine's `_load_bearing_ok` and in
`validate()`; scalar/batch parity holds with the flag on (tests). Verified:
the 10-stack now packs only what physics allows (max transitive load ratio
0.95 vs 8.57 before). (b) Floor deck rule, NOT flag-gated (inert when
overhang is off): a z=0 placement must have deck-contact ≥ the effective
support ratio — enforced in the JIT check (raw deck dims passed only under
overhang, sentinel 0 = legacy machine path), the decoders' new-bin bypasses,
v2 `feasible`, and `validate()`. (c) The service forces the v2 warm-start for
instances ≤ 60 boxes (`use_v2_seed=True` in the adapter) — v2's layer builder
tiles exact-fit instances the greedy rotation argmax deterministically broke
(2×2×2 packed 7/8 on every seed; now 8/8). *Consequence:* stacked fragile
loads pack FEWER boxes than before — physically correct; kill-switch env for
the old behavior. *Verified:* extended backend-equivalence sweep (2670
comparisons, 0 mismatches, both new branches proven fired), BR smoke
bit-identical, flags-off golden bit-identical. *Deferred (explicit NO-GO this
round):* decoder rotation-score shaping (the F18 root fix) — requires another
twin campaign; the v2-seed mitigation covers the measured case and the TIPPED
residual stays documented.

### D17 — Load-model completeness: block overlay, under-fill rule, repair-then-ship — **ACCEPTED (implemented)**
Round-3 adversarial evaluation (docs/08_hardening_round3.md) proved the load
model had two remaining holes shared identically by BOTH decoder twins —
invisible to backend equivalence by construction — plus a v2 bookkeeping bug,
and that the service *detected* the resulting invalid plans and shipped them
anyway. At service defaults, 3/30 stack-heavy solves returned physically
overloaded plans (up to 1.6×), and the volume fitness actively *selects* such
plans because an overloaded stack packs more. *Implemented (all
unconditional — these are physics bugs, not features):*
(a) **F20 block sibling overlay:** the block decoder's Phase 2c checked every
column of a k×l block against the SAME pre-block loads and then committed all
of them — a shared supporter never saw the aggregate (each check passed 10 kg
while 40 kg landed). Checks now read `placement_top_loads + blk_inc`, a
caller-owned overlay into which each ACCEPTED column's contribution is
accumulated (via the existing apply routines pointed at the overlay) before
the next sibling is checked. No float-subtraction rollback — the overlay is
wiped, the real commit is unchanged.
(b) **F21 under-fill rule:** a box placed later with its top plane exactly at
an existing box's bottom becomes a NEW supporter and physically inherits a
contact-share of that rider's outflow (`share = out_R · a/(T_old + a)`) —
previously unchecked and unbooked (a fragile mlot=0 box could be slid under a
loaded slab). Every placement path now computes the inherited load, rejects
when it exceeds the candidate's own `max_load_on_top`, flows
`weight + inherited` down the transitive dry-run/commit, and books it on the
candidate's row. Old supporters are deliberately NOT debited — strictly
conservative, no negative propagation, no float dust. Blocks with riders on
any column top shrink to a single box (exact semantics); mirrored in v2
(`feasible`/`_commit`). Zero-weight riders contribute nothing, keeping
weightless workloads bit-identical.
(c) **F22 v2 diamond flow:** v2's recursive `_propagate_load` added a
re-converged node's second share but blocked its onward distribution —
everything below a diamond junction undercounted forever, so the (exact)
dry-run check disagreed with v2's own commit. Replaced by the same
accumulate-then-distribute worklist the dry-run uses; `regen_top_load`
inherits the fix.
(d) **F23 repair-then-ship:** the ONLY result-side `validate()` used to be
the postprocess pre-gate, which logged "failed pre-pass validation; skipping"
and served the plan unchanged. `adapter.solve` now validates every final
plan; on failure it deterministically strips the riders feeding the
overloaded carrier (topmost first — never leaves a hovering box; ≤ n
iterations) into `unpacked` with reason `load_limit_repair`, re-validates,
and surfaces everything in a response `warnings` field + ERROR log. With the
engine fixes, repair firing at all indicates an unknown engine bug worth
reporting — "done" now means *physically valid, or repaired with warnings*.
(e) **F25 scale-aware epsilon:** every load/weight comparison used `+ 1e-6`
absolute, which is below one double ulp for limits ≥ ~4.5e9 (contract allows
1e12) — exactly-at-limit stacks flipped on accumulation-order noise. All
sites (twins, v2, validate, repair) now use `max(1e-6, 1e-9·limit)`
(`models.load_tol`); identical behavior for limits ≤ 1e3. The core gate also
bounds finite `max_load_on_top` to the same 1e15 cap as weights.
*Golden policy change:* F20/F21 fix the DIRECT model too, so the historical
"flags-off bit-identical" invariant is replaced by "**load-invariant
scenarios bit-identical** (zero weights / infinite mlot — provably unaffected:
zero-weight riders contribute nothing) + **load-constrained scenarios
physics-reviewed re-baseline**" (precedent: the floor-first fix df2ec2c). BR
stays bit-identical by construction (no load constraints → geometric
decoders untouched).
*Verified:* extended backend equivalence PASS (2670 comparisons, 0
mismatches; new coverage counters block_joint/underfill/epsilon all fired) +
the NEW standing physics gate `scripts/_verify/verify_load_physics.py`
(14,544 exact-oracle decodes: 90 violations pre-fix → **0** post-fix) + BR
smoke bit-identical. The physics gate exists because equivalence testing is
STRUCTURALLY blind to twins that are identically wrong — that is how F20/F21
survived two rounds.

*Round-5 addendum (2026-07-04, hardening round 4 → 5):* round-4 evaluation
confirmed the D17 fixes hold (300-case repair fuzz + 7,200-decode
rider-hostile physics fuzz, 0 violations) and closed the leftovers:
(a) the pallet-weight cap tolerance is now `load_tol` on ALL four surfaces
(v2 `feasible`, `validate()`, both JIT twins) — previously three different
epsilons could make an engine-legal at-limit heavy plan ship with spurious
`warnings` (R1); (b) ops robustness: fail-fast settings validation
(0 < SOFT < HARD, limits > 0 — a typo'd `PALLET_API_HARD_BUDGET_S=0` used
to turn every job into `timeout` with a green /health), responsive
solver-crash detection (a child dead without a result is reported in
<1 s, not after the full 120 s hard budget), pre-body rate limiting +
a poll limiter + env-gated CORS (R2/R3/R4); (c) reporting honesty:
floaters no longer claim `supported_by: ["floor"]`, floor boxes under
overhang report actual deck contact (R5). *Deferred with measurements
(R7):* the F21 rider scan costs ~2× RAW decode throughput at n=500 on
plane-aligned worst-case geometry (~20% at n=200, noise at n≤60) but has
ZERO measured end-to-end effect (identical service wall times and
bit-identical solution quality in fixed-budget A/B) — the designed fix
(a per-pallet distinct-bottom-z plane set as a pre-filter, so the O(n)
rider scan only runs when the candidate's top matches an existing plane)
stays in the backlog until profiling ever shows decode-bound solves.

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
| Decoder-level rotation tie-break (D14 TIPPED residual) | deferred (Numba+Cython twins + BR re-validation) | 3/22 battery scenarios flag it |
| ~~Automated test suite~~ | done | two-tier suite, 93 tests (`tests/README.md`) |
