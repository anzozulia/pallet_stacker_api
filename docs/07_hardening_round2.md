# 07 — Hardening round 2 (post-hardening adversarial evaluation, 2026-07-03)

Round-2 adversarial evaluation of the hardened service (after `ef1cfa2` /
upstream `4f817bc`): 20-scenario hostile battery with independent geometry
validation, protocol/concurrency probes, two code audits (one independent).
This doc is the findings inventory and what shipped for each. Full narrative:
upstream `docs/reports/36_hardening_round2.md`; decisions: ADRs D15 (amended)
and D16.

## Findings → fixes

| ID | Sev | Finding | Fix (shipped this round) |
|----|-----|---------|--------------------------|
| F16 | HIGH | Spatial dims unbounded → int64 wraparound tiers: OverflowError `solver_error` ≥2⁶³; garbage JIT support/load math ≥~3e9 (invalid plans served `done`); batch-vs-scalar fitness divergence ≥~2.1e6 | Dims ≤ **1e6**: schema `le` + core gate `MAX_DIM` (all int64 products provably fit) |
| F17 | HIGH | z=0 unconditionally "supported" → with overhang, floor boxes placed 100% off-deck, floating (probe: 6/12 at 0% deck contact) | Deck-contact ≥ effective support ratio for floor placements when overhang active — JIT check + new-bin bypasses (both twins), v2 `feasible`, `validate()`. Inert at overhang=0 |
| F18 | MED | Greedy rotation argmax breaks exact tilings: 2×2×2 perfect fit packed 7/8 deterministically (all seeds/budgets) | Mitigation: adapter forces the v2 warm-start for ≤60-box instances (v2 tiles it; now 8/8). Root fix (decoder score shaping): explicit NO-GO this round, still deferred |
| F19 | MED | Direct-only load model: 10-stack of legal links → bottom box at 8.6× its `max_load_on_top`. v2's check-direct/commit-transitive model shares the hole (never rejects a fresh column) | `transitive_load_bearing` flag (core OFF, service ON): transitive dry-run **check** + transitive commit in both twins, v2, and `validate()`. 10-stack now maxes at 0.95× |
| L1 | LOW | `pack(time_limit_s=0)` falsy → unbounded | `is not None` |
| L2 | LOW | Stage budgets (MIP 2×30s, ejection/consolidation 5s, layer loop) not clamped to the deadline | `_clamp_to_deadline` + in-loop check (library-only; service stages OFF) |
| L3 | LOW | arq cancellation re-enqueues (≤5×) while the old solve subprocess still runs | `max_tries = 2` + comment |
| L4 | LOW | Result-TTL race between `status()` and `result()` → `failed/solver_failed` | → 404 `not_found` |
| L5 | LOW | `fastapi>=0.110` too loose for the 413 counting path's re-raise dependency | `>=0.115` |
| L6 | QoL | `options: null` → 422 | Normalised to defaults (= omission) |
| L7 | DOC | Group larger than one pallet silently strands the remainder | Documented (contract + OpenAPI): groups are never split |

**Held up under attack (no action):** needle forest, paper stack, 500×128-char
unicode ids at the body cap (exact volumetric max packed), huge seeds, weight
exactly at cap, all-fragile floor max, sr=0 with centroid, `rotations:none`
unpackable→clean reasons, 100 groups×5 co-located, 150 giants across exactly
100 pallets in budget, burst 20×100 jobs (all done 71 s, no starvation),
cross-worker determinism byte-identical, body cap live on both paths, `/docs`
through the middleware.

**Pre-existing baseline anomalies (unchanged before/after, not ours):**
upstream `verify_backend_equiv_geom.py` mode4/5 exceptions (script drift) and
`verify_batch_consistency.py`'s NON-INT-dims divergence (5.3e-05 — non-integer
dims are outside the input contract, D7). `_BATCH_AVAILABLE` in dispatch.py is
set but never consulted — constrained solves crash on hosts without built
Cython extensions (Docker deployments unaffected).

## Verification

- Extended `scripts/_verify/verify_backend_equiv_cstr.py` (raw-deck args,
  TRANSITIVE sweep dim, coverage counters, deterministic round-2 battery):
  **2670 comparisons, 0 mismatches**; 180 overhang-active + 180 transitive-on
  instances; deck branch fired 5×, transitive branch fired 5× (counters
  gate the PASS).
- BR smoke bit-identical; flags-off golden bit-identical (r1 vs r2 image);
  regression-equivalence counts byte-identical to the pre-change baseline.
- Service suite **108 passed** (5 new load-model tests incl. physics oracles;
  scalar/batch parity ×transitive); oracle: 10-stack max load ratio 8.57
  (flag off, the defect) → 0.95 (flag on); overhang: zero off-deck floor
  boxes.

## Deferred (unchanged backlog)

Decoder rotation-score shaping (F18 root; TIPPED_IN_LAYER residual),
symmetric overhang convention, queue-saturation 503, trust_proxy, result
caching, geometric-decoder deck rule (library-only surface).
