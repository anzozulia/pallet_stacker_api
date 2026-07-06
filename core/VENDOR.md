# Vendored core — `pallet_packer`

This directory is a **vendored copy** of the `pallet_packer` BRKGA packing engine,
so this service is fully self-contained: `git clone` + `docker compose up --build`
builds the core (Cython extensions) into the image with no external dependency.

- **Package:** `pallet-packer` 3.13.0
- **Vendored from:** `pallet_stacker_research` @ commit `fd2ea2c` (2026-06-01),
  plus two floor-first placement fixes (upstream `00df520` and `9d9ab07`):
  (1) the single-box placement finders prefer the lowest z on ties; (2) the
  block decoder picks the largest floor footprint before stacking height
  (`find_best_block_at_pos` + `blocks.py`), so identical boxes on a tall pallet
  spread into flat layers instead of a corner tower. BR1-7 verified
  density-neutral (bit-identical). Plus the realism layer (upstream `9be8f1a`,
  2026-07-02): recenter + orientation-alignment post-passes
  (`postprocess.py`), the epsilon-scaled secondary realism fitness
  (`_brkga_core/realism.py`), a gated heavy-first seed, and a realism
  tie-break in the v2 candidate set — all default OFF in the core (the
  service enables them; see ADR D14 and upstream `docs/reports/34`).
  Flag-OFF default behavior verified bit-identical pre/post. Plus the
  hardening pass (upstream `4f817bc`, 2026-07-02): align-pass dependents
  skip + `validate()` raised to engine feasibility parity,
  `PalletPacker.pack(time_limit_s=...)` deadline + budget-aware v2 seeding
  (fixes the homogeneous-constrained capacity cliff), clamped
  `realism_weight`, a `weight ≥ 1e15` gate guard (the 1e18 `_NO_LIMIT`
  sentinel), recenter/CoG-envelope interaction, a global post-pass budget,
  and duplicate-id realism defense — see ADRs D14/D15 and upstream
  `docs/reports/35`. Flag-OFF golden and BR smoke re-verified
  bit-identical. Plus round 2 (upstream `f18bb6f`, 2026-07-03) — the first
  decoder-twin change: spatial dims bounded at 1e6 (int64 wraparound guard),
  the floor deck-contact rule under overhang (no more off-deck floating
  floor boxes), and flag-gated transitive load bearing
  (`transitive_load_bearing`, default OFF; the service enables it) with a
  transitive dry-run check + commit in BOTH decoder twins, v2, and the
  validator — see ADR D16 and upstream `docs/reports/36`. Verified by the
  extended backend-equivalence campaign (2670 comparisons, 0 mismatches,
  new branches proven fired), BR smoke + flags-off golden bit-identical.
  Plus round 3 (upstream `f0bb308`, 2026-07-03) — load-model completeness,
  the second decoder-twin change: the block decoder's sibling-column
  overlay (a k×l block can no longer jointly crush a shared supporter each
  per-column check individually passed), the under-fill rider rule (a box
  slid beneath an already-placed one inherits — and must be able to carry —
  its contact-share of that load, both twins + v2), the v2 diamond-flow
  commit fix, scale-aware load tolerances (`models.load_tol`), the
  consulted `_BATCH_AVAILABLE` scalar fallback, and the NEW
  `pallet_packer/repair.py` (deterministic post-hoc load repair the service
  uses as its serve-time backstop) — see ADR D17 and upstream
  `docs/reports/37`. Verified by the extended equivalence campaign (2670
  comparisons, 0 mismatches, block/under-fill/epsilon counters gate the
  PASS) and the NEW standing physics gate
  `scripts/_verify/verify_load_physics.py` (90 violations pre-fix →
  0/14,544 post-fix); BR smoke + all three goldens bit-identical.
  Plus rounds 4–5 (upstream `65cf0e6`, 2026-07-04): the round-4 evaluation
  confirmed the load model holds (0 violations across 7,500 targeted
  probes); round 5 unified the pallet-weight-cap tolerance with
  `models.load_tol` across validate + both twins (spurious at-limit
  warnings), made `to_json` honest about floaters and overhang deck
  contact, and added load-bearing guard comments — see upstream
  `docs/reports/38`. Equivalence + physics gates PASS, BR smoke and all
  goldens bit-identical.
  Plus round 6 (upstream `5ad25ff`, 2026-07-06) — the third decoder-twin
  change: the floor TOPPLING rule (F30) completes round-2's F17 — a floor
  box under overhang must keep its footprint centroid over the deck-contact
  rectangle (a `support_ratio < 0.5` box could otherwise ship tipping past
  the deck edge), added to v2 `feasible`, both JIT twins (the shared
  `_check_load_on_top` floor branch), and `validate()`, gated on
  `require_centroid` and provably inert with overhang off or
  `support_ratio >= 0.5` (goldens/BR/battery unaffected). Plus the
  multi-pallet OBJECTIVE (F31, pure-Python, no twin): the BRKGA fitness
  was pallet-0-only, so `max_pallets > 1` without groups packed pallets
  2..N with no search signal — now unpacked-volume ≫ pallet-count ≫
  realism over all pallets, byte-identical at `max_pallets == 1`. See ADRs
  D18/D19 and upstream `docs/reports/39`. Verified by the extended
  equivalence campaign (2670 comparisons, 0 mismatches, new
  `floor_com_rejections_seen` counter gates PASS) and the standing physics
  gate's new FLOOR_TOPPLE oracle + toppling-regime sweep (2,402 violations
  pre-fix → 0/18,864 post-fix); BR smoke + all three goldens bit-identical.
  Kept in sync with upstream.
- **Contents:** source only — the `pallet_packer/` package (`.py` + Cython
  `.pyx`/`.pxd`) plus its build files (`setup.py`, `pyproject.toml`). No built
  artifacts (`.so`/`.c`/`.html`/numba caches) are committed; the Docker build
  compiles them. The two batch decoders use OpenMP (`-fopenmp`), so the runtime
  image needs `libgomp1` (see `deploy/Dockerfile`).

## How it's built

`deploy/Dockerfile` runs `pip wheel ./core` in a builder stage (compiling the 6
Cython extensions) and installs the resulting wheel into the runtime image. The
service imports `pallet_packer` as a normal installed package — the only import
seam is `src/pallet_api/solver/adapter.py`.

## Updating

To refresh the engine, re-copy the source from upstream and bump the commit above:

```bash
rsync -a --prune-empty-dirs \
  --include='*/' --include='*.py' --include='*.pyx' --include='*.pxd' --exclude='*' \
  /path/to/pallet_stacker_research/pallet_packer/ core/pallet_packer/
cp /path/to/pallet_stacker_research/{setup.py,pyproject.toml} core/
```
