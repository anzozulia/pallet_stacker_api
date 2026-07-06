# 12 — Hardening round 8 (evaluation + fix), 2026-07

Round-8 adversarial evaluation after round 7 (`cf53bb2` / upstream
`eb73742`), aimed at the interactions the round-7 CoG change created and the
post-decode code paths that run AFTER the CoG-constrained decode. A 51-agent
find→adversarially-verify sweep across 9 surfaces plus independent
cross-checks. The round-7 fix held; two residuals in the certifying validator
were fixed, both LOW / safety-net-only.

## Findings → fixes

| ID | Sev | Finding | Fix |
|----|-----|---------|-----|
| F36 | LOW (safety-net) | **Per-ASSEMBLY toppling under overhang** (the ADR-D20 deferred residual, now hand-proven). Every guard is per-single-box (F30; centroid-over-*supporter*, which can itself overhang) or whole-pallet (F35). A detached sub-tower rooted on an overhanging floor box tips over the deck edge while a disjoint counterweight keeps the whole-pallet CoG central. 3-box proof: whole-pallet CoG x=473.3 ∈[0,1200] (all gates pass) but component {A,B} CoG x=1220 > deck edge 1200. | Add a per-assembly check to `validate()` §8 (pure Python, no twin): partition by the VERTICAL support graph, require each assembly's weighted CoG inside the convex hull of its own deck-contact region. Gated == F30 (overhang + require_centroid), provably inert otherwise. The reachability probe decided scope: 0 engine-produced tips ⇒ validate-only certifier (engine-side reject deferred). ADR D21 |
| F37 | LOW (core-lib only) | **`validate()` §7 stricter than the engine for a single-weighted-box pallet with an explicit CoG range.** v2 `_cog_ok` bypasses the first box; the JIT cstr new-bin path commits the seed box with no envelope check (jit_decoders_cstr.py:318-360). So the engine legally corner-places a lone box while validate flagged it → reject-legal false positive. Service immune (never sets cog ranges; under overhang F30 pins a lone floor box into the envelope). | `validate()` §7 skips a single-placement pallet (`len(placements) > 1`), mirroring the engine's first-box bypass. ADR D21 |

## The corrected model (why the obvious F36 fix is wrong)

The naive "per-connected-component CoG via union-find over FACE contacts"
sketched as the D20 residual is physically INCORRECT: a vertical side face
transmits no restraint against tipping outward, so it would (a) false-MERGE a
side-abutting counterweight into the tipping component and compute a central
CoG that masks the tip, and (b) miss two side-by-side stacks that each tip on
their own. The correct partition joins boxes ONLY through the vertical
resting-on relation (`validate._contact_area`'s `abs(z−z2)>EPS` guard already
excludes side faces). Each floor-rooted assembly's CoG is checked against the
convex hull of its own deck-contact rectangles (the physically-correct support
polygon; the bounding rect would be too permissive in a multi-floor gap).

## Reachability probe (the scope-deciding gate)

`scratchpad/probe_r8_subassembly.py` runs the FULL `brkga_pack_v35` at the
SERVICE config (`cog_envelope_fraction=0.5, cog_check_min_load_fraction=0.0,
allow_pallet_overhang=True, realism on`) over 210 adversarial instances
(counterweight-forcing + big-floor-heavy-top + chaos generators), and checks
each RAW engine plan with two independent detectors: `validate()` §8, and a
reimplemented convex-hull oracle. Result: **0/210 engine-produced tips** — the
running-CoG reject blocks the tipping box on every natural order unless the
counterweight lands first, and heavy-high placement is realism-penalised. Same
standard of evidence as F35's 0/160. So the validate-only certifier is
proportionate; the engine-side per-assembly reject (twinned, F30-style) stays
deferred, contingent on the probe ever finding a reachable case.

## Verified clean (no fix)

- **Config forwarding intact.** The round-7 CoG config is passed verbatim into
  every grouped pallet decode (`driver._pack_with_groups` → per-pallet
  `_brkga_pack_v35_impl(..., config, ...)`) and every multibin pallet;
  `apply_postprocess` runs once and reverts per pallet. No grouped-overhang
  regression.
- **Twins bit-identical.** `_check_cog_envelope_njit` and the F30 floor-centroid
  branch match Numba↔Cython; round 7 touched no twin and none drifted.
- **Numeric.** CoG accumulation error proven < EPS; the `0.0*inf → nan`
  min-load gate is guarded at every site (v2 `max_w is not None`, JIT
  `< _NO_LIMIT`, validate mirrors v2).
- **Post-passes.** Every recenter/align mutation is mirrored by a whole-pallet
  re-validate + full revert; the recenter corner-jam under overhang is reverted,
  not a defect.
- **Ops.** The F32 drain race is not-reachable; overhang detection can't
  disagree (pydantic-normalised non-negative int).
- **Defense-in-depth (left + documented).** The serve-time repair pipeline
  repairs LOAD only; a CoG/geometry residual ships as `status:done` + an
  advisory `unrepaired:` warning with no revert/drop. Dormant under the
  believed-correct engine (v2 over-books load via `_rider_inflow`, so validate
  never sees an overload it passed → repair never fires). §8 is already
  protective on the postprocess path. Making `solve()` drop-not-ship is a
  larger serve-path change, deferred.

## Gate evidence

Both fixes are `validate.py` (pure Python, NO Cython twin). Service suite green
(host + Redis); ruff clean; upstream equivalence 2670/0 unchanged (validate has
no twin — the cstr CoG path is already swept) and the physics gate unaffected
(no new oracle criterion in the validate-only path); BR smoke + all three
goldens bit-identical (F36/F37 provably inert without overhang); realism
battery 19/22 unchanged (`overhang_case` stays balanced → PASS). Probe
artifacts: scratchpad `probe_r8_subassembly.py`, `round8_eval.mjs`,
`f36_sanity.py`.
