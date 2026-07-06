# pallet-packer-api-service

A free, open, no-login HTTP microservice that packs boxes onto pallets/containers
in 3D. Submit a packing job, poll for the result — no accounts, no history, just
the tool. It wraps the verified `pallet_packer` BRKGA engine (vendored into this
repo, so the whole thing is self-contained) behind an asynchronous job API.

> **Status: working MVP (v0.1.0).** End-to-end async service — FastAPI + Redis/arq
> workers, hard per-solve timeout, per-IP rate limiting, structured logging.
> Load-tested and tuned ([`docs/05_load_profile.md`](docs/05_load_profile.md)),
> covered by an automated test suite, and fully documented via OpenAPI (`/docs`).

## What it does

- **Input:** a list of boxes (integer dimensions) + a pallet/container spec +
  optional constraints (weights, fragility, support ratio, grouping, max pallets,
  overhang).
- **Output:** a 3D packing plan — where each box goes, on which pallet, in which
  orientation — plus per-pallet utilisation, centre-of-gravity, the support graph,
  and any unpacked items.
- **How:** an asynchronous job API — `POST /pack` → `job_id` → poll
  `GET /jobs/{id}` — backed by Redis and a pool of solver workers, because a solve
  can take tens of seconds.

**Not in scope (by design):** authentication, accounts, persisted history, billing.
Anonymous, ephemeral, open to everyone. A web frontend is a separate future project.

## Quick start

**Requirements:** Docker + Docker Compose v2. Nothing else — the packing core is
vendored and built into the image (the first build compiles its Cython extensions,
~2–4 min; later builds are cached).

```bash
git clone <this-repo> && cd pallet_packer_api_service
docker compose up --build            # redis + api (:8000) + 4 solver workers
```

Check it end-to-end:

```bash
bash scripts/smoke_e2e.sh            # submit -> poll -> done, plus a 422 and a 400
```

**Interactive API reference:** open <http://localhost:8000/docs> (Swagger UI) — it
fully documents every endpoint, field, and error from the live OpenAPI schema.
Also `/redoc` and `/openapi.json`; `/` redirects to `/docs`.

## Using the API

All endpoints are under `/api/v1`, JSON in/out, no auth.

```bash
# 1. submit a job -> 202 with a job_id
curl -s localhost:8000/api/v1/pack -H 'content-type: application/json' -d '{
  "boxes": [
    {"id": "B1", "length": 300, "width": 200, "height": 150, "weight": 2.0},
    {"id": "B2", "length": 400, "width": 300, "height": 300, "weight": 5.0}
  ],
  "pallet": {"length": 1200, "width": 1000, "height": 1500, "max_weight": 900},
  "options": {"max_pallets": 1, "time_budget_s": 10}
}'
# -> {"job_id":"…","status":"queued","links":{"self":"/api/v1/jobs/…"}}

# 2. poll until terminal: queued -> running -> done (with "result") | failed | timeout
curl -s localhost:8000/api/v1/jobs/<job_id>
```

- Spatial dimensions are **positive integers ≤ 1e6** (any unit — mm/cm/inch —
  applied consistently; 1e6 ≈ 1 km in mm); weights may be fractional (≤ 1e12,
  finite). Omit a cap (`max_weight`, `max_load_on_top`) for *unlimited*. Up to
  **500 boxes** and **10 MB** of body per request (both configurable).
- **Physical load model:** box weights propagate down the whole stack against
  `max_load_on_top` (transitive), and boxes always rest on the deck even with
  `max_overhang` — stacked fragile loads pack fewer boxes than a naive
  direct-only model would suggest. Since round 3 (ADR D17) the model is
  *complete*: block placements can't jointly crush a shared supporter, a box
  slid under an already-placed one inherits (and must be able to carry) its
  share of that load, and every `done` result has passed replay validation —
  in the rare case it hasn't, the service repairs the plan (offenders move to
  `unpacked_items` with `reason: "load_limit_repair"`) and reports it in a
  `result.warnings` array. `warnings` present = engine bug worth reporting.
  Round 6 (ADRs D18/D19) added the floor **toppling** rule (a box that could
  only sit tipping past the deck edge under overhang is dropped, not shipped
  as "valid") and a real **multi-pallet objective** (max_pallets > 1 now
  optimises unpacked volume ≫ pallet count ≫ layout, instead of only the
  first pallet). Round 7 (ADR D20) extended toppling to whole
  **sub-assemblies**: with `max_overhang > 0` the pallet's weighted CoG is
  held over the deck footprint, so a load that could only pack by putting its
  balance past the deck edge is packed balanced or returned unpacked.
- Errors share one envelope: `{"error": {"code", "message"?, "problems"?}}`.
  Schema violations → `422` — **including unknown/typo'd fields** (a
  misspelled option is rejected, never silently defaulted); contract
  violations (duplicate id, over-cap) → `400`; oversized body → `413`.
- **Fragile + orientation-sensitive goods need both knobs:** `max_load_on_top: 0`
  caps stacked *weight* only — add `rotations: "this_side_up"` to also stop the
  box being tipped on its side.
- Same input + same `seed` ⇒ identical plan. On highly symmetric loads (e.g.
  all-identical boxes) *different* seeds may legitimately return identical plans.

See [`docs/02_api_contract.md`](docs/02_api_contract.md) for the rationale; the
OpenAPI schema at `/docs` is the authoritative contract.

## Configuration

All operational knobs are environment variables (12-factor); defaults shown. Copy
[`.env.example`](.env.example) to `.env` to override.

| Variable | Default | Purpose |
|---|---|---|
| `PALLET_API_REDIS_URL` | `redis://localhost:6379` | Redis (queue + result store) |
| `PALLET_API_MAX_BOXES` | `500` | Box cap (over → `400`) |
| `PALLET_API_MAX_BODY_BYTES` | `10485760` | Request-body byte cap (over → `413`) |
| `PALLET_API_SOFT_BUDGET_S` | `90` | Solver time budget (caller `time_budget_s` ceiling) |
| `PALLET_API_HARD_BUDGET_S` | `120` | Worker hard wall-clock kill (→ `timeout`) |
| `PALLET_API_RATE_LIMIT_PER_MIN` | `30` | Per-IP `POST /pack` per 60-second window (checked BEFORE the body is read) |
| `PALLET_API_POLL_RATE_LIMIT_PER_MIN` | `600` | Per-IP `GET /jobs/*` polls per minute |
| `PALLET_API_CORS_ORIGINS` | *(empty)* | Comma-separated browser origins; empty = no CORS middleware |
| `PALLET_API_RESULT_TTL_S` | `3600` | How long a result is fetchable (then `404`) |
| `PALLET_API_DEFAULT_MAX_PALLETS` | `1` | Default `max_pallets` |
| `PALLET_API_DEFAULT_SEED` | `42` | Default RNG seed (determinism) |
| `PALLET_API_RECENTER` | `1` | Centre the finished load on the deck (D14) |
| `PALLET_API_ALIGN_ORIENTATIONS` | `1` | Unify same-SKU rotations per layer (D14) |
| `PALLET_API_REALISM_WEIGHT` | `1.0` | Heavy-low/anti-tower fitness term; `0` = off (D14) |
| `PALLET_API_TRANSITIVE_LOAD` | `1` | Weights propagate down the whole stack (D16); `0` = old direct-only model |
| `OMP_NUM_THREADS` | `2` | OpenMP threads **per worker** (set on the worker) |

The worker pool also reads BRKGA knobs (`PALLET_API_POPULATION_SIZE`, …); see
`.env.example` and [`docs/03_design_decisions.md`](docs/03_design_decisions.md).

## Architecture

Stateless FastAPI API → Redis (queue + job status + result store, with TTL) → a
pool of arq workers (each runs one solve at a time, with a hard wall-clock kill in
a subprocess). N workers ⇒ N concurrent solves. Full design + failure modes in
[`docs/01_architecture.md`](docs/01_architecture.md).

```
client ──HTTP──▶ API (stateless) ──enqueue──▶ Redis ──pull──▶ workers (arq)
                  GET /jobs/{id} ◀──result/status──┘            └─ pallet_packer solve
```

## Connect an external front-end

A front-end running in a **separate compose project** on the same Docker host can
reach the API container-to-container — no host ports, no public round-trip. The
`api` service joins a shared bridge network, **`pallet-packer-net`**, with the
stable alias **`pallet-packer-api`**; redis and the workers stay on the private
`default` network and are not reachable from the front-end.

1. Bring this stack up first — it **creates** the shared network:
   ```bash
   docker compose up -d --build
   ```
2. In your front-end's compose file, declare the network as `external` and join
   it (full copy-paste in [`examples/frontend-compose.example.yml`](examples/frontend-compose.example.yml)):
   ```yaml
   services:
     frontend:
       networks: [default, pallet-packer-net]
       environment:
         PALLET_API_BASE_URL: "http://pallet-packer-api:8000/api/v1"
   networks:
     default:
     pallet-packer-net:
       external: true      # created by the pallet API stack above
   ```
3. Your front-end's **server-side** code calls `http://pallet-packer-api:8000/api/v1/…`.

> This is for **server-to-server** calls (SSR / a BFF / a proxy inside your
> front-end container). A user's browser is not on the Docker network — for
> direct browser calls, route them through your front-end's reverse proxy or the
> public host port, and note the API ships **no CORS headers** (add a proxy or
> CORS middleware if a browser must call it cross-origin).
>
> Bring the API stack down *after* the front-end (or `docker compose down`
> harmlessly warns that `pallet-packer-net` still has active endpoints).

## Development

The supported run path is Docker. For host-side work:

```bash
make install      # service + dev deps (pip install -e ".[dev]")
make lint         # ruff
make test         # host-runnable tests (core/redis/e2e tiers auto-skip)
make test-docker  # the FULL suite inside the container (real core + Redis)
make up / down    # docker compose up --build / down
make logs / ps    # follow logs / list services
make smoke        # end-to-end smoke against a running stack
make loadtest     # Phase 6 scaling burst (docs/05_load_profile.md)
make help         # list all targets
```

Tests are two-tier (host vs container) because the core's compiled extensions only
build/run on Linux — see [`tests/README.md`](tests/README.md).

```
pallet_packer_api_service/
├── core/                vendored pallet_packer engine (source; built in the image)
├── src/pallet_api/      the service package
│   ├── api/             FastAPI routes + app
│   ├── schemas/         pydantic request/response models
│   ├── solver/          adapter to the core (the ONLY import seam) + hard-timeout runner
│   ├── workers/         arq worker + solve task
│   ├── jobs/            (reserved) · config/  settings
├── deploy/Dockerfile    multi-stage: build the vendored core, slim runtime
├── docker-compose.yml   redis + api + 4 workers (standalone)
├── docs/                planning + design + load profile
├── examples/            front-end cross-stack networking example
├── scripts/             smoke + load-test harness
└── tests/               two-tier pytest suite
```

## Deployment notes

- `docker compose up --build` is restart-safe (all state is in Redis).
- **Scaling:** capacity = number of workers. The tuned default is **4 workers ×
  `OMP_NUM_THREADS=2`** for an ~8–10 core host; keep total worker-threads ≤ cores.
  Adjust `deploy.replicas` and `OMP_NUM_THREADS`. Rationale + numbers in
  [`docs/05_load_profile.md`](docs/05_load_profile.md).
- The API tier is stateless and scales independently; Redis is a single instance
  for the MVP (the result store is ephemeral).

## The packing core

The 3D packing is done by `pallet_packer`, a hybrid BRKGA engine with
Cython-accelerated decoders, **vendored** under [`core/`](core/) (see
[`core/VENDOR.md`](core/VENDOR.md)) so this repo is fully self-contained. It is
independently verified (validator-clean for integer input, deterministic at a fixed
seed). Honest quality: physically valid packings, ~1.7 pp behind 2013 full-support
academic SOTA — fine for a free, general-purpose tool. This service does not
reimplement any packing logic; it adds the HTTP/async/scaling/operational layer.

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/00_PLAN.md`](docs/00_PLAN.md) | Vision, scope, goals, success criteria |
| [`docs/01_architecture.md`](docs/01_architecture.md) | Components, data flow, scaling, failure modes |
| [`docs/02_api_contract.md`](docs/02_api_contract.md) | Endpoints, request/response JSON, error model |
| [`docs/03_design_decisions.md`](docs/03_design_decisions.md) | The decisions log (ADR-style) |
| [`docs/04_roadmap.md`](docs/04_roadmap.md) | Phased build plan + status |
| [`docs/05_load_profile.md`](docs/05_load_profile.md) | Load test: scaling sweep + recommended config |

## License

MIT — see [`LICENSE`](LICENSE).

## Contributing

Issues and PRs welcome. Please run `make lint` and `make test-docker` (the full
suite) before opening a PR; keep the core's import seam confined to
`src/pallet_api/solver/adapter.py`.
