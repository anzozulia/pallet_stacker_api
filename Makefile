# pallet-packer-api-service — dev convenience targets.
# The supported run path is `make up` (Docker): the core's compiled extensions
# are Linux/OpenMP and won't load on a plain host. `make run`/`make worker` are
# for a host that already has the core importable (PYTHONPATH=/path/to/core +
# libgomp). See docs/04_roadmap.md.

.PHONY: help install dev lint test test-docker test-e2e run worker up down smoke loadtest

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install:  ## Install the service + dev deps (editable)
	pip install -e ".[dev]"

lint:  ## Lint + format check
	ruff check src tests

test:  ## Run host-runnable tests (core/redis/e2e tiers skip without them)
	pytest

test-docker:  ## Run the FULL suite inside the container (real core + Redis)
	docker compose -f docker-compose.loadtest.yml run --rm -v "$$(pwd)":/app -w /app api \
		sh -c "pip install -q pytest httpx && pytest"
	docker compose -f docker-compose.loadtest.yml down

test-e2e:  ## Bring up the stack, run the e2e tests against it, tear down (needs host pytest)
	docker compose up -d --build
	@for i in $$(seq 1 60); do curl -fsS localhost:8000/api/v1/health >/dev/null 2>&1 && break; sleep 1; done
	-pytest tests/test_e2e.py
	docker compose down

run:  ## Run the API locally (needs the core importable; Docker is preferred)
	uvicorn pallet_api.api.app:app --reload

worker:  ## Run a solver worker locally (needs the core importable)
	arq pallet_api.workers.settings.WorkerSettings

up:  ## docker compose up: redis + api + 2 workers (builds the image)
	docker compose up --build

down:  ## docker compose down
	docker compose down

smoke:  ## Run the end-to-end smoke test against a running stack
	bash scripts/smoke_e2e.sh

loadtest:  ## Phase 6 load test: scaled stack + a burst (see docs/05_load_profile.md)
	LT_OMP=2 docker compose -f docker-compose.loadtest.yml up -d --build --scale worker=4
	python3 scripts/loadtest.py burst --jobs 24 --boxes 60 --budget 90 --label 4wx2t
	docker compose -f docker-compose.loadtest.yml down
