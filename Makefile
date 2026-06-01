# pallet-packer-api-service — dev convenience targets.
# The supported run path is `make up` (Docker): the core's compiled extensions
# are Linux/OpenMP and won't load on a plain host. `make run`/`make worker` are
# for a host that already has the core importable (PYTHONPATH=/path/to/core +
# libgomp). See docs/04_roadmap.md.

.PHONY: help install dev lint test run worker up down smoke

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install:  ## Install the service + dev deps (editable)
	pip install -e ".[dev]"

lint:  ## Lint + format check
	ruff check src tests

test:  ## Run the test suite
	pytest -q

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
