# pallet-packer-api-service — dev convenience targets.
# Most targets are placeholders until the corresponding roadmap phase lands
# (see docs/04_roadmap.md). They document the intended dev workflow.

.PHONY: help install dev lint test run worker up down

help:  ## Show available targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

install:  ## Install the service + deps (Phase 1+)
	pip install -e ".[dev]"

lint:  ## Lint + format check (Phase 1+)
	ruff check src tests

test:  ## Run the test suite (Phase 1+)
	pytest -q

run:  ## Run the API locally (Phase 2+)
	@echo "TODO (Phase 2): uvicorn pallet_api.api.app:app --reload"

worker:  ## Run a solver worker locally (Phase 3+)
	@echo "TODO (Phase 3): arq pallet_api.workers.settings.WorkerSettings"

up:  ## docker compose up: api + redis + workers (Phase 5+)
	@echo "TODO (Phase 5): docker compose -f deploy/docker-compose.yml up"

down:  ## docker compose down (Phase 5+)
	@echo "TODO (Phase 5): docker compose -f deploy/docker-compose.yml down"
