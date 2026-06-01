# Vendored core — `pallet_packer`

This directory is a **vendored copy** of the `pallet_packer` BRKGA packing engine,
so this service is fully self-contained: `git clone` + `docker compose up --build`
builds the core (Cython extensions) into the image with no external dependency.

- **Package:** `pallet-packer` 3.13.0
- **Vendored from:** `pallet_stacker_research` @ commit `fd2ea2c` (2026-06-01)
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
