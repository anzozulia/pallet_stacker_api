# 06 — Hardening plan (post-adversarial evaluation, 2026-07-02)

Findings from the adversarial evaluation of the realism layer (29-scenario
hostile battery with independent geometry validation, old-vs-new image timing
differentials, a v2 warm-start scaling sweep, and two code audits with live
probes). This document is the fix plan: every finding, its root cause, the
chosen fix, and the order of work. Nothing below is implemented yet.

Legend: **origin** = `new` (introduced by the realism layer, commit b8778b3)
or `pre` (pre-existing, exposed by this evaluation).

---

## 1. Finding inventory

| ID | Sev | Origin | Finding | Component |
|----|-----|--------|---------|-----------|
| F1 | HIGH | new | Align pass safety net weaker than the engine: `validate()` lacks centroid-support, per-box `requires_full_support`, transitive load, CoG envelope; dependents gate blocks only dz changes, so a yaw swap can pull the footprint out from under a dependent (probe-confirmed: dependent at support 0.0, validate clean) | postprocess.py, validate.py |
| F2 | HIGH | pre | Capacity cliff: v2 warm-start has NO time budget and is superlinear on identical boxes (N=100→9s, 200→40s, 300→101s, 400→175s). Auto-enabled by any finite constraint → homogeneous constrained loads ≥ ~300 boxes always hit the 120 s hard kill (old image: 185.5 s, new: 186.4 s — not a regression). Phase 6 validated the cap on a mixed catalogue only. Same cause: 1 s-budget 200-box request runs 21 s | packer.py, driver.py |
| F3 | MED | new | `realism_weight > 2` silently breaks the bounded-loss guarantee (ε unclamped → dropping the smallest box becomes profitable) | realism.py |
| F4 | MED | pre | `options.seed = -5` accepted (202) then fails as `solver_error: ValueError` from `np.random.default_rng` — internal-looking error for a trivial input mistake | schemas/requests.py |
| F5 | MED | pre | `_NO_LIMIT = 1e18` sentinel: box weights ≥ 1e18 are silently unpackable even on an UNLIMITED pallet (service always uses the constraint decoder path) | precompute.py / schema |
| F6 | MED-HIGH | pre | Unbounded strings + body: 100 KB box id round-trips the whole pipeline (probe-confirmed); 500×100 KB ids ≈ 50 MB body with several-hundred-MB transient RAM amplification across uvicorn/pydantic/Redis/pickle | schemas, app |
| F7 | MED | new (library-only) | `recenter_pass` targets deck centre even when the caller set `pallet.cog_x_range`/an active CoG envelope; `validate()` has no CoG check so the revert never fires. Service unaffected (envelope off by default) | postprocess.py |
| F8 | LOW | new | Align time-box is per pallet (2 s × K pallets + O(N²) validates); many-pallet results can eat into the 30 s hard-kill margin; interacts with unbounded `max_pallets` | postprocess.py |
| F9 | LOW | new (library-only) | Duplicate box ids desync scalar vs batch realism (id-keyed vs positional); service path unaffected (gate enforces unique ids) | realism.py |
| F10 | LOW | pre | `group: ""` is not-None → all empty-string-group boxes forced onto ONE pallet as a co-location group | adapter.py |
| F11 | LOW | pre | `max_overhang` unbounded (5000 → boxes placed fully off the deck, contract-"valid"); plus the demonstrated one-sided +x/+y overhang cantilever (2×2 spanning 0–1400 on a 1200 deck, CoG +17%, unfixable by recentering since span > deck) | schema; core convention |
| F12 | LOW | pre | `weight: Infinity` passes pydantic (`ge=0`) → caught by the gate as 400 instead of a clean schema 422 | schemas |
| F13 | LOW | pre | Rate limiter: Redis error escapes as a bare 500 outside the error envelope; keys on `request.client` only (no `X-Forwarded-For`) — known from the earlier research/ADR D13 follow-up | limits.py |
| F15 | LOW | pre | Engine-internal inconsistency discovered during A2: the v2 packer enforces TRANSITIVE load propagation (`_propagate_load`) but the BRKGA constraint decoders enforce DIRECT-supporter load only (`_apply_load_contribution_njit` never propagates down) — a tall stack legal for the BRKGA path can exceed a bottom box's cumulative limit under the v2 model. `validate()` therefore stays direct-only (no-stricter-than-the-weakest-engine-path rule). Real fix = transitive accumulation in the JIT commit (both twins + BR revalidation) — grouped with the deferred decoder work | jit_constraints.py |
| F14 | DOC | — | Expectations: fragile boxes get tipped on their sides to fit more (fragility ≠ orientation; shippers need `this_side_up` too); `seed` has no observable effect on symmetric loads (all flat layouts tie on realism as well); D5's "500 boxes is safe" holds only for mixed/unconstrained loads | docs |

**Held up under attack (no action):** exact-fit, 1×1×1 dust (realism
auto-disabled yet still centred), needles/pancakes/slivers, weight exactly at
cap, `support_ratio = 1.0` boundary, NaN/Infinity dims handling, huge seeds,
budget clamps, TSU-too-tall, group co-location under overflow, pallet-count
discipline (60 cubes → 2 pallets, not 50). New-code overhead on big
unconstrained solves: +5 s at N=500, mostly the intentional LS budget scaling.

---

## 2. Phases

### Phase A — P0 correctness (core; ~half-day)

**A1 (F1). Footprint-preserving dependents gate.** In
`align_orientations_pass`, when a box has dependents, only allow swaps with
dx, dy AND dz unchanged (i.e. effectively nothing to do besides identical-dims
rotation relabeling) — a footprint change under a dependent is never locally
repairable. This alone kills the dependent-stranding class.

**A2 (F1). Harden `validate()` to match the engine's feasibility stack:**
- per-box `requires_full_support`: `min_support = 1.0 if box.requires_full_support else cfg.support_ratio` (mirrors packer.py:210);
- centroid-over-supporter check, gated on `cfg.require_centroid_supported`;
- a box at z > 0 with zero supporter contact is ALWAYS invalid, regardless of `support_ratio` (fixes the sr=0 floater hole);
- ~~transitive load propagation~~ AMENDED during implementation: load stays DIRECT-supporter — the BRKGA decoders' commit model is direct-only (see F15), so a transitive validate would falsely reject decoder-legal results; with A1's hard dependents-skip, direct-only is sufficient for the post-pass gate;
- CoG envelope check, gated on explicit `pallet.cog_x_range`/`cog_y_range` ONLY (amended: the config-fraction default of 0.25 is not enforced on the geometric decoder path, so validating it would reject engine-legal geometric packings).

*Safety argument:* engine outputs already satisfy all of these (enforced at
placement), so hardening only tightens the post-pass gate and catches engine
bugs better. Verified by re-running the flag-off golden (bit-identity) and the
upstream verify scripts. Risk check: audit all `validate()` callers first
(post-pass, upstream verify scripts, any replay-validation) for behavioral
coupling.

**A3 (F4). `seed` schema bound:** `ge=0` on `OptionsIn.seed` → clean 422; doc
line in the field description.

**A4 (F3). ε clamp:** `eps = 0.5·(min_vol/cap)·min(1.0, realism_weight)` in
`build_realism_context`; docstring + `models.py` comment updated (weight is a
dial in [0,1], values above 1 are clamped); property test.

### Phase B — P1 capacity cliff (core; ~1 day)

**B1 (F2). Deadline in the v2 packer.** Add an optional wall-clock deadline to
`PalletPacker.pack()` (new `time_limit_s` parameter; None = today's unbounded
behavior, so default is bit-identical). Check it between candidate-generation
stages and per-box inside the greedy EP loops; on expiry, return the best
candidate found so far.

**B2 (F2). Budget-aware v2 seed in the driver.** Give Phase 0 a slice of the
solve budget (e.g. `min(0.5 × time_limit_s, remaining)`) as the v2 deadline,
and skip the v2 seed entirely when the slice is clearly insufficient for the
instance size (guard on N and slice, verbose-logged). This also fixes
`budget_micro` (1 s budget honored within spawn overhead).

**B3 (F2/F14). Docs:** amend D5 ("500 is safe" → safe for mixed loads;
homogeneous constrained loads now bounded by the v2 deadline instead of the
hard kill) and `05_load_profile.md`.

**B4. Regression scenarios:** add `cap_500_identical_capped`, `dust_500`, and
`budget_micro` to the acceptance battery with a "completes within the soft
budget, status done" gate.

### Phase C — P2 service robustness (service; ~half-day)

**C1 (F4/F5/F6/F11/F12). Schema bounds** in `requests.py`:
`id`/`group` `max_length=128`; `weight`/`max_load_on_top`/`max_weight`
`le=1e12` + `allow_inf_nan=False` on float fields (Infinity → clean 422);
`max_overhang` bounded by a model validator to `≤ min(pallet.length,
pallet.width)`; `max_pallets le=100`. OpenAPI descriptions updated.

**C2 (F6). Body-size cap:** ASGI middleware rejecting bodies > 10 MB with a
413 in the standard error envelope (fills the documented 413 gap in the API
contract at the same time).

**C3 (F10). Normalize `group: ""` (and whitespace-only) to `None`** in
`adapter._box`.

**C4 (F5). Core gate guard:** reject `weight ≥ 1e15` in `input_validation`
(out-of-range) so library callers can't hit the sentinel either.

**C5 (F7). Recenter/envelope interaction:** skip `recenter_pass` when the
pallet has explicit `cog_x_range`/`cog_y_range` or the config envelope is
active (comment: the envelope owner decides placement; recentering to deck
centre may violate it).

**C6 (F8). Global post-pass deadline:** `apply_postprocess` gets one shared
wall-clock budget (default 4 s) threaded into every align pass, instead of 2 s
per pallet.

**C7 (F9). Duplicate-id defense:** `build_realism_context` returns None (term
disabled, logged) when duplicate ids are detected — parity guarantee preserved
for un-gated library callers.

**C8 (F13). Rate-limiter fail-safe:** wrap the Redis INCR/EXPIRE in
try/except → 503 `degraded` envelope on Redis failure (matches `/pack`'s own
degraded path). `X-Forwarded-For`/`trust_proxy` stays a deferred follow-up
(deployment-dependent).

### Phase D — docs & expectations (~1 hour)

- **D1 (F14):** README + OpenAPI: fragile goods that are orientation-sensitive
  need `max_load_on_top: 0` **and** `rotations: "this_side_up"`; seed
  determinism nuance (identical layouts across seeds on symmetric loads is
  expected).
- **D2:** ADR updates (D5 amendment, D13 note, D14 addendum listing F1/F3
  fixes); `core/VENDOR.md` bump; upstream report `35_hardening.md` documenting
  A1/A2/A4/B1/B2/C4 (the core-touching fixes) with the same patch-here →
  copy-upstream → verify → commit-both discipline.

### Phase E — verification gates (per phase, then final)

1. New unit tests: yaw-swap-under-dependent reverted (A1); validate-hardening
   cases — centroid-over-hole, RFS under-support, transitive-load overload,
   sr=0 floater (A2); ε clamp property (A4); seed=-5 → 422 (A3); schema-bound
   422s, `group:""`, body cap 413 (C); v2 deadline: N=300/400 identical
   constrained completes inside the soft budget with status done (B).
2. Flag-off golden bit-identity vs the pre-change image (A2 and B1 must not
   perturb default outputs; B1's deadline default None is inert).
3. Full container suite + 19-scenario realism battery (16/19 baseline holds)
   + the adversarial battery re-run (target: 29/29 clean, or documented WARNs
   only).
4. Upstream: sync changed core files, run `verify_realism_layer.py` +
   `verify_floor_first_fix.py`, BR spot-check unaffected paths, commit both
   repos (upstream first for the hash, then VENDOR.md here).

---

## 3. Explicit non-goals (deferred, unchanged from D14)

- **Symmetric overhang convention** (the real fix for F11's one-sided
  cantilever): coordinate-convention change across validator, dispatch,
  packer, and both decoder twin families + BR revalidation. Only worth it if
  overhang-centred loads become a requirement.
- **Decoder-level rotation tie-break** (the TIPPED_IN_LAYER residual, 3/19
  battery scenarios) and brick-bond interlock.
- Queue-saturation 503, result caching, `trust_proxy` — pre-existing roadmap
  items, unrelated to these findings.

## 4. Suggested execution order

A (P0 correctness) → E-gates for A → B (capacity cliff) → E-gates for B →
C (service robustness) → D (docs) → final E (full verification + upstream sync
+ commits). A and C are independent and could swap; B depends on nothing in A.
Estimated total: ~2 working days including verification.
