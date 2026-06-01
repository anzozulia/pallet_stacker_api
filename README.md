# pallet-packer-api-service

A free, open, no-login HTTP microservice that packs boxes onto pallets/containers
using the verified [pallet_packer](../) BRKGA algorithm. Submit a packing job,
poll for the result — no accounts, no history, just the tool.

> **Status: working (v0.1.0), pre-launch.** The async service is implemented and
> runs end-to-end: FastAPI API + Redis/arq job queue + a pool of solver workers,
> with a hard per-solve timeout, per-IP rate limiting, and structured logging.
> Remaining before launch: a load test + tuning pass (roadmap Phase 6) and an
> automated test suite. See [`docs/04_roadmap.md`](docs/04_roadmap.md).

## What this is

- **Input:** a list of boxes (integer dimensions) + a pallet/container spec +
  optional constraints (weights, fragility, support, grouping).
- **Output:** a 3D packing plan — where each box goes, on which pallet, in which
  orientation — plus utilisation, centre-of-gravity, and any unpacked items.
- **How:** an asynchronous job API (`POST /pack` → `job_id` → poll
  `GET /jobs/{id}`), backed by Redis and a pool of solver workers.

## What this is NOT (by design, for the MVP)

No authentication, no user accounts, no persisted history, no billing. Anonymous,
ephemeral, open to everyone. The web frontend is a **separate** future project
that will consume this API.

## Quick start

The supported run path is Docker (the core's compiled extensions are
Linux/OpenMP). From this directory:

```bash
docker compose up --build          # redis + api (:8000) + 2 solver workers
bash scripts/smoke_e2e.sh          # end-to-end check: submit -> poll -> done, + a 400
```

Then drive it directly:

```bash
# submit a job
curl -s localhost:8000/api/v1/pack -H 'content-type: application/json' -d '{
  "boxes":[{"id":"B1","length":300,"width":200,"height":150,"weight":2.0}],
  "pallet":{"length":1200,"width":1000,"height":1500},
  "options":{"max_pallets":1,"time_budget_s":5}}'
# -> {"job_id":"…","status":"queued","links":{"self":"/api/v1/jobs/…"}}

# poll for the result
curl -s localhost:8000/api/v1/jobs/<job_id>      # queued -> running -> done (with result)
```

Config is environment-driven — see [`.env.example`](.env.example) and
[`docs/03_design_decisions.md`](docs/03_design_decisions.md) (D9). Full HTTP
surface: [`docs/02_api_contract.md`](docs/02_api_contract.md).

## The core algorithm (dependency)

This service is a thin, production-grade wrapper around the `pallet_packer`
package (the separate core repo). That algorithm is independently verified
(see the core repo's `docs/reports/30`–`32`): correct and validator-clean for
real-world **integer-dimension** input, deterministic, with a known feasible
ceiling of **~500 boxes**. This service does not reimplement any packing logic;
it adds the HTTP/async/scaling/operational layer around it.

## Documentation (read in order)

| Doc | What it covers |
|---|---|
| [`docs/00_PLAN.md`](docs/00_PLAN.md) | Vision, scope, goals, non-goals, success criteria |
| [`docs/01_architecture.md`](docs/01_architecture.md) | System design, components, data flow, scaling, failure modes |
| [`docs/02_api_contract.md`](docs/02_api_contract.md) | Endpoints, request/response JSON, error model |
| [`docs/03_design_decisions.md`](docs/03_design_decisions.md) | The decisions log (ADR-style) and their rationale |
| [`docs/04_roadmap.md`](docs/04_roadmap.md) | Phased build plan, milestones, acceptance criteria |
| [`docs/05_load_profile.md`](docs/05_load_profile.md) | Phase 6 load test: scaling sweep, recommended config, criteria validation |

## Repository layout

```
pallet_packer_api_service/
├── docs/                     planning documents (above)
├── src/pallet_api/           the application package
│   ├── api/                  HTTP routes (FastAPI)
│   ├── schemas/              request/response models (pydantic)
│   ├── jobs/                 job lifecycle, queue, status, result store
│   ├── workers/              solver worker (arq) — runs one solve per worker
│   ├── solver/               adapter to the pallet_packer core
│   └── config/               settings
├── deploy/                   Dockerfile, docker-compose, deployment config
├── scripts/                  dev/ops scripts
└── tests/                    unit + integration + load tests
```

## Tech stack

FastAPI · uvicorn · arq (Redis-backed async jobs) · Redis · pydantic · the
`pallet_packer` core. See [`docs/03_design_decisions.md`](docs/03_design_decisions.md).
