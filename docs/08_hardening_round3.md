# 08 — Hardening round 3 (post-round-2 adversarial evaluation, 2026-07-03)

Round-3 adversarial evaluation of the stack after `e927541` / upstream
`f18bb6f`: three independent code audits, a 16-scenario live battery with
transitive-load physics oracles, a NEW decode-level randomized physics
fuzzer (14,400 raw decodes against an exact independent oracle — the gate
class that backend equivalence structurally cannot provide), and
deterministic repros for every confirmed finding. This doc is the findings
inventory and what shipped. Full narrative: upstream
`docs/reports/37_hardening_round3.md`; decisions: ADR **D17**.

**Headline:** the load-bearing model had two engine holes shared identically
by both decoder twins, the volume fitness actively *selects* the resulting
overloaded plans (they pack more), and the service *detected* them (the
postprocess pre-gate) — then logged a warning and shipped them as `done`.
Measured end-to-end at service defaults: **3/30 stack-heavy solves returned
physically overloaded plans** (up to 1.6× a box's `max_load_on_top`); one
was served live by the lab stack during the evaluation.

## Findings → fixes

| ID | Sev | Finding | Fix (shipped this round) |
|----|-----|---------|--------------------------|
| F20 | HIGH | Block decoder Phase 2c checked each of a k×l block's columns against the SAME pre-block loads, then committed all — a shared supporter never saw the aggregate (each check saw 10 kg, 40 kg landed; direct mode pre-existing, transitive m× worse). Fitness converges TO the violation. | Sibling-column overlay (`blk_inc`): checks read `top_loads + overlay`; each accepted column accumulates into the overlay before the next sibling is checked. Both twins. |
| F21 | HIGH | "Under-fill": a box placed later with its top at an existing box's bottom becomes a NEW supporter, inheriting a contact-share of its load — never checked, never booked (confirmed trace: fragile mlot=0 box slid under a loaded slab, all 6 modes + v2). | Rider-inflow rule at every placement path: reject if inherited > own mlot; flow `weight+inherited` downward (transitive); book on own row; conservative (old supporters not debited). Blocks with riders shrink to exact single-box. Twins + v2. |
| F22 | MED | v2 `_propagate_load` visited-set dropped flow below diamond junctions — everything below undercounted forever; v2's own (exact) dry-run check disagreed with its commit. Exposed via the forced v2 seed (all n≤60). | Accumulate-then-distribute worklist (same walk as the dry-run); `regen_top_load` inherits. |
| F23 | MED | The only result-side `validate()` was the postprocess pre-gate: on failure it logged "skipping" and served the invalid plan as `done`, no signal (live-confirmed: exactly 1 worker warning == the served overloaded diamond). | `adapter.solve` validates every final plan; deterministic **repair** (strip riders feeding the overload, topmost-first, → `unpacked` reason `load_limit_repair`) + `warnings` field + ERROR log. "done" = physically valid or repaired-with-warnings. |
| F24 | MED | `dispatch._BATCH_AVAILABLE` set but never consulted — on hosts without compiled Cython, every constrained solve died with NameError on generation 0 (contradicting the documented numba fallback). | `decode_population_fitness` falls back to per-chromosome scalar decodes (plan bit-identical to the batch path; pinned by test) + one-time warning. |
| F25 | LOW | `+1e-6` absolute load epsilons vanish ≥ ~4.5e9 (one ulp at 1e12 is 1.22e-4): exactly-at-limit stacks flip on accumulation order; engine-pass/validate-fail possible at high weights. Core gate also didn't bound finite `max_load_on_top` at all. | Scale-aware `max(1e-6, 1e-9·limit)` at all comparison sites (twins, v2, validate, repair; `models.load_tol`); gate bounds finite mlot < 1e15. |
| F26 | LOW | Reported utilisation divides by RAW deck capacity while placements occupy overhang-inflated volume → can exceed 100%; fitness scale leaves [0,1] (monotone — selection unaffected). | Documented (contract note). Fitness cap deliberately unchanged: rescaling would perturb tie-epsilon selection for zero user value. |
| F27 | QoL | Unknown/typo'd request fields silently ignored — `{"suport_ratio": 0}` → 202 solving at 0.8 with no signal. | `extra="forbid"` on all request models → 422 naming the field (breaking for sloppy clients; documented). |
| F28 | LOW | The F18 v2-seed mitigation silently vanished for requested budgets < 2 s (exact-fit 8 → 7/8 live) and above the n≤60 gate (72 boxes → 66/72); group solves split the budget per pallet. | Adapter floors small-instance budgets at 2 s (they finish faster anyway) → exact-fit 8/8 at any requested budget. Gate boundary + groups amplifier documented. |
| F29 | notes | Scalar geometric modes use effective dims, batch geometric raw dims (unreachable via driver); >120 s internal overrun would double compute via arq retry (tail risk). | mlot gate bound shipped (above); rest recorded. |

**Held up under attack (no action):** dims at the 1e6 bound (single giant
4.0 s; 500×1e6³ → 100 pallets 61.5 s; per-pallet volume exactly 1e18 exact —
no int64 wrap, no OOM, no dimension-proportional allocation anywhere);
budget clamp verified at both ends (1e9 → ceiling, 1e-9 → 1 s floor);
cross-run determinism byte-identical on the new code paths; round-2 deck
rule at max overhang incl. RFS (zero off-deck floor boxes); transitive pure
columns; sr=0 towers; fragile groups legally floored via rotation; huge
seeds (SeedSequence); align/recenter edit gates (per-swap validate +
dependents-skip + snapshot revert; recenter provably never decreases deck
contact); the round-2 transitive walk internals (scratch hygiene, mixed
bottom-z ordering, zero-contact guards, dispatch arg threading — all refuted
at line level).

## Verification

- Extended `verify_backend_equiv_cstr.py` (deterministic block-sibling,
  under-fill, and 1e12-epsilon cases; coverage counters
  `block_joint_rejections_seen` / `underfill_rejections_seen` /
  `epsilon_scale_seen` gate the PASS): **2670 comparisons, 0 mismatches**,
  all counters fired. A pre-fix run of the same script FAILS with a
  round-3 coverage hole — recorded as the defect proof.
- NEW standing gate `scripts/_verify/verify_load_physics.py` (upstream):
  14,544 exact-oracle decodes across all 6 modes — **90 violations pre-fix
  (51 mixed + 23 at service defaults + 16 deterministic) → 0 post-fix**.
  Exists because backend equivalence is structurally blind to twins that
  are identically wrong — how F20/F21 survived rounds 1–2.
- BR1/3 smoke bit-identical to the tracked checkpoint (geometric decoders
  untouched by construction).
- Golden policy (ADR D17): load-invariant scenarios (zero weights /
  infinite mlot) bit-identical; the load-constrained golden legitimately
  shifts and was re-baselined with review.
- Service suite green incl. new tests: repair determinism + adapter
  warnings path (`test_repair.py`), v2 diamond exactness + under-fill
  (`test_v2_loadflow.py`), scalar fallback plan parity
  (`test_dispatch_fallback.py`), unknown-field 422s, budget floor + live
  exact-fit at 1.9 s.
- Round-3 re-runs: hostile battery diamond scenario now physically legal;
  end-to-end violation rate 0/30 (was 3/30); production-settings fuzz
  0/7200 (was 23/7200).

## Deferred (unchanged backlog)

Decoder rotation-score shaping (F18 root; TIPPED_IN_LAYER residual, 3/22
battery), symmetric overhang convention, queue-saturation 503, trust_proxy,
result caching, geometric-decoder deck rule (library-only surface),
scalar/batch geometric overhang-dims unification (unreachable via driver).
