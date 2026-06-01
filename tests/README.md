# Tests

Two tiers, because the `pallet_packer` core ships as compiled Linux/OpenMP
extensions that only import inside the Docker image.

| File | Tier | Needs |
|---|---|---|
| `test_schemas.py` | host | nothing — pure pydantic |
| `test_loadtest_helpers.py` | host | nothing — stdlib harness helpers |
| `test_adapter.py` | docker | core (request → gate → solve → `to_json`; **Phase 1 acceptance**) |
| `test_runner.py` | docker | core (hard-timeout subprocess kill, D6) |
| `test_worker.py` | docker | core (arq `solve_job` task) |
| `test_routes.py` | docker | core + Redis (HTTP contract via `TestClient`) |
| `test_e2e.py` | live stack | a running stack on `:8000` (submit → poll → **done**) |

Each docker-tier file calls `pytest.importorskip("pallet_packer")`, so on a host
without the core they **skip** (not error). Redis-dependent tests use the
`redis_ready` fixture and skip when no Redis is reachable. `test_e2e.py` skips
unless a stack answers at `PALLET_API_E2E_BASE` (default `http://localhost:8000/api/v1`).

## Running

```bash
# Host: runs the pure tiers, skips the rest with reasons.
make test            # == pytest

# Full suite inside the container (real core + Redis):
make test-docker

# End-to-end against a live stack:
make up              # in another shell, or: docker compose up -d --build
make test-e2e        # brings the stack up, runs test_e2e.py, tears it down
```

`make test-docker` mounts the working tree into the image and runs `pytest` there
with Redis available; `tests/` is excluded from the built image (`.dockerignore`),
so the mount is how the tests get in.
