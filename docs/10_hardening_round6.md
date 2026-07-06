# 10 — Hardening round 6 (evaluation + fixes), 2026-07-04/06

Round-6 adversarial evaluation of the stack after round 5 (`8c827f5` /
upstream `65cf0e6`), deliberately aimed at the LEAST-probed subsystems
(rounds 3–5 had exhaustively fortified the load model): a 500-scenario
full-service-path physics fuzzer (postprocess + repair in the loop, oracle
written independently of `validate.py`), a targeted floor-toppling probe,
and two breadth audits (multi-pallet/group correctness; serve-time
reporting + glue).

**Headline: the load/support/geometry model held (0 violations / 0 crashes
in 500 full-path scenarios) — but two MEDIUM findings surfaced outside it:
a genuine physics gap in the overhang floor rule (F30) and a search-quality
blindness on the multi-pallet feature (F31).** All findings fixed this
round; nothing deferred.

## Findings → fixes

| ID | Sev | Finding | Fix |
|----|-----|---------|-----|
| F30 | MED | A floor box under `max_overhang > 0` with `support_ratio < 0.5` could ship with its footprint centroid PAST the deck edge — physically toppling — as a validate-clean, warning-free `done` plan (confirmed live: centroid x=410 vs deck edge 400, 53% of mass cantilevered). Round-2 F17 added the deck-contact RATIO rule but not the toppling half; the stacked centroid rule exempts floor boxes. Reachable at will: 105/120 solves at sr<0.5. Provably impossible at the default sr=0.8 (ratio ≥ 0.5 ⟹ centroid over deck; 0/90 empirical) | Centroid-over-deck-contact rule on all four surfaces (v2 `feasible`, both JIT twins — ONE shared floor-branch function all decoder paths call — and `validate()`), gated on `require_centroid_supported`, integer-exact in the twins, boundary (centroid ON the edge) accepts. ADR D18 |
| F31 | MED | The fitness (base + realism, scalar + batch) masked to `pallets[0]`: at `max_pallets>1` without groups, packing 10 boxes on pallet 1 scored IDENTICALLY (0.975) to dropping them — the search was provably blind past the first pallet, and the restart/v2-hybrid comparisons amplified it. Undocumented; never invalid | Gated multi-bin objective (`max_pallets != 1`): unpacked volume ≫ pallet count ≫ realism-over-all-pallets, with the dominance-hierarchy proof in ADR D19; byte-identical historical formula at `max_pallets == 1`. **A/B: +173 boxes packed over 16 solves, 0 worse, same pallet counts, all valid** |
| F32 | MED-LOW | The runner's timeout path had no post-deadline queue drain: a result enqueued in the sub-ms window AT the hard deadline was dropped as `timeout` — or, child put-then-exited, a SUCCESSFUL solve was mislabeled `solver_crashed` | One `q.get_nowait()` drain after the deadline loop breaks, before the timeout/crashed decision; `_ctx` test seam (round-5 `_target_fn` precedent) + 3 deterministic stub-context tests |
| F33 | LOW | `support_ratio` could exceed the documented [0,1] (2.0 confirmed) when degraded geometry double-counts overlapping supporters — only on plans already served WITH `warnings` | Documented honestly (schema description + contract + io.py comment), not clamped — consistent with the round-5 utilisation precedent |
| F34 | LOW | `io.py` `supports` lacked a `q is not p` guard (a zero-height box listed itself; unreachable via the integer-dims gate) + the obfuscated `any(... for _ in [None])` | Guard added; construct de-obfuscated to the plain boolean (semantics identical) |
| — | LOW | `_pack_with_groups` silently dropped 10 tuning params (`generations`, elite/mutant fractions, migration cadence, sku-aware/adaptive toggles) for group solves — library-only | Forwarded verbatim (defaults identical → untuned callers bit-identical) |
| — | notes | Oversized group strands its overflow while pallets sit empty (correct per strict co-location — already documented in the contract); repair leaves non-contiguous pallet ids after dropping an emptied pallet (cosmetic); repair is O(P³) outside the budget (backstop-only) | Doc notes only (ADR D19 tail) |

## Verified clean (no defect found)

- The full load/support/geometry model: 500 full-service-path scenarios
  (overhang/fragile-tower/exact-tile/multipallet-group/chaos families,
  2 seed bases), oracle independent of validate.py — 0 violations,
  0 crashes.
- Group co-location is structurally guaranteed (never a silent split);
  `apply_postprocess` runs exactly once per top-level solve on every path
  (the restart/group recursions call the un-postprocessed `_impl` — the
  docstring claim was verified true).
- Serve-time glue: adapter `repaired_ids` labeling (globally unique ids →
  no mislabel), `_json_safe` nested-inf recursion, routes status mapping
  (`timeout` surfaced, TTL race → 404), schemas match `to_json` output.

## Gate evidence

- **Physics gate** (`verify_load_physics.py`, extended): FLOOR_TOPPLE
  oracle criterion (gated on the config's centroid flag) + a third
  "C-topple" sweep (low sr + full overhang). Pre-fix: FAIL with 2,402
  violations / 18,864 decodes (C: 2,277/4,320; the sr=0.8 production sweep
  stayed at 0 — empirically confirming the safe boundary). Post-fix: 0.
- **Backend equivalence** (`verify_backend_equiv_cstr.py`, extended):
  deterministic round-6 battery (mode 2 — the scoring that actually picks
  partially-overhanging floor spots, verified pre-fix) with a
  `floor_com_rejections_seen` counter gating PASS, incl. the boundary pair
  (centroid ON the edge accepts / one half-grid past rejects) and an
  rc=0 gate case.
- **Bit-identity**: all three goldens (max_pallets=1, no overhang), BR
  smoke (geometric path), the realism battery (ratio ≥ 0.92 on its only
  overhang scenario), and group solves are provably outside both fixes'
  active regimes.
- Service suite: 135 passed host-tier + the Redis-backed run at E6;
  scalar/batch multibin parity |Δ| < 1e-12.

Probe artifacts: session scratchpad `fuzz_r6_fullpath.py`,
`probe_floor_topple.py` (+`_hi`), `ab_multipallet_quality.py` (+ JSON
results), `r6_physics_prefix.log` (the pre-fix defect proof),
`round6_findings.md` (the full dossier).
