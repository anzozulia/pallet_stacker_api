# 09 — Hardening round 4 (evaluation) → round 5 (fixes), 2026-07-04

Round-4 adversarial evaluation of the stack after round 3 (`ce998ef` /
upstream `f0bb308`): two independent line-level audits (the round-3 diff
itself; a fresh-eyes systemic pass over io/serialization, worker lifecycle,
config parsing, middleware order), a 300-case repair fuzzer, a 7,200-decode
rider/overlay-hostile physics fuzz, decoder perf + fixed-budget quality
A/Bs between the r2 and r3 images, and clean-machine battery re-runs.

**Headline: no physics holes.** The round-3 load model survived everything,
including fuzzers purpose-built against its newest code (0 violations in
7,500 probes). Round-4 findings are ops-robustness and consistency class;
all shipped in round 5 except R7 (deferred with measurements).

## Findings → fixes (round 5)

| ID | Sev | Finding | Fix |
|----|-----|---------|-----|
| R1 | MED-LOW | F25's epsilon unification missed the pallet-weight cap: v2 `feasible` used `load_tol`, `validate()` used `EPS`, the twins used `1e-6` — engine-legal at-limit heavy plans (v2 is force-seeded for every n≤60 solve) could ship with spurious `warnings` + ERROR log, unrepairable (repair handles only `max_load_on_top`) | `load_tol` on all four surfaces (validate + both twins; v2 already had it) |
| R2 | MED | Budget envs un-cross-validated: `HARD_BUDGET_S=0`/negative/`HARD<SOFT` boots cleanly then every job → `timeout` with `/health` green — silent total outage | Fail-fast `_validate()` at settings import (both API and worker refuse to boot); also rejects zero/negative limits, body cap, TTL |
| R3 | MED-LOW | A child that dies WITHOUT enqueueing (segfault/OOM-kill) pinned its worker for the full 120 s before `solver_crashed` | Runner waits in 0.25 s slices watching child liveness (+ final drain for the enqueue-then-die race; kill-escalation in `finally`) |
| R4 | MED-LOW | `/pack` limiter ran AFTER full body receive+parse; 413s never counted; `GET /jobs/*` uncapped; no CORS despite the front-end compose story | `RateLimitMiddleware` (outermost, pre-body, same keys/envelopes) + poll limiter (`PALLET_API_POLL_RATE_LIMIT_PER_MIN`, 600) + env-gated CORS (`PALLET_API_CORS_ORIGINS`, default off) |
| R5 | LOW | Floaters reported `supported_by: ["floor"]` alongside `support_ratio: 0.0`; floor boxes under overhang hardcoded `support_ratio: 1.0`; two OpenAPI descriptions promised utilisation "[0,1]" | `[]` for unsupported z>0 boxes; actual deck-contact fraction under overhang; descriptions now say "may exceed 1.0 under `max_overhang`" |
| R6 | LOW | Bare `PALLET_API_X=` (empty value) read as False, silently disabling default-ON features; `RATE_LIMIT_PER_MIN=0` → permanent 429; bodylimit docstring claimed live tunability | Empty/unset → default; zero limits refuse to boot; docstring fixed |
| R7 | LOW | Rider-scan decode throughput: ~2× slower RAW decodes at n=500 plane-aligned worst case (922→496/s scalar, 1807→958 chrom/s batch; ~20% at n=200; noise ≤60) | **Deferred by decision** — zero end-to-end effect measured (identical wall times; fixed-budget quality A/B bit-identical: 196/500 packed, util 0.7458, both seeds, both images). Designed fix on file: per-pallet distinct-bottom-z plane-set pre-filter |
| R8 | notes | Shrink-fallback rider re-check silently load-bearing; no-Cython fitness-tie plan divergence; stale validate.py docstring; repair's direct-model cross-branch stripping | Guard comments in both twins; notes in dispatch/repair docstrings; docstring fixed |

## The battery-timing autopsy (round 4's false alarm)

The round-3 gate run showed realism-battery walls up to 19× round-2 values
(budget_micro 4.5 s → 87.3 s), reproduced EXACTLY on an idle machine —
deterministic, therefore queueing, not CPU. Cause: the battery polls 4
scenarios concurrently, and the `--force-recreate` that deployed the r3
image silently reset the lab stack from 4 workers to 1. At
`--scale worker=4` every wall returned to its round-2 value to the decimal
(cap_500 51.2 vs 51.3, dust 31.6 vs 31.7, budget_micro 4.5 vs 4.5). Lesson
codified: always bring the lab stack up with `--scale worker=4`.

## Held up under attack (no action)

Repair invariants (300 random broken plans: termination, load-clean,
conservation, determinism, no new hovers — 0 failures); rider/overlay
physics under plane-aligned/block/overhang/multi-pallet geometry (7,200
decodes, 0 violations); conservative booking has no wrong-accept path;
`blk_inc` provably all-zero at every rider-scan site; the io.py `supports`
comprehension (weird style, correct semantics); recenter integer-grid
safety; result-pipe deadlock hypotheses; rotation permutation table;
`_json_safe` NaN/inf coverage; arq dead-worker retry semantics.

## Deferred (unchanged backlog + new entries)

F18 root / TIPPED residual; queue-saturation 503 backpressure; the long-queued
expiry edge; trust_proxy; result caching; R7 plane pre-filter (with
measurements); `_top_load` id()-keying invariant (documented).

> **Round-7 correction to the queued-expiry framing:** the exact observable is
> NOT a `404`. arq's queue zset (`arq:queue`) never expires, so a job stuck
> queued past the ~24 h job-data TTL (`arq:job:{id}`, arq's `expires_extra_ms`
> default) keeps reporting `status: queued` indefinitely; when a worker finally
> pops it, its data is gone and arq writes a `JobExecutionFailed('job expired')`
> → the client sees `{"status":"failed","code":"solver_failed","message":"job
> expired"}`, i.e. **stuck-`queued` → `job expired` failure**, not a 404. The
> result TTL (~1 h) does NOT prematurely 404 a still-queued job. Only triggers
> after >24 h of saturation/outage.
