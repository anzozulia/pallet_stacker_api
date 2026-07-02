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
