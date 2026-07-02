"""arq WorkerSettings — run with: arq pallet_api.workers.settings.WorkerSettings"""
from __future__ import annotations

from arq.connections import RedisSettings

from pallet_api.config import settings
from pallet_api.workers.tasks import solve_job


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    functions = [solve_job]
    # One solve at a time per worker process; scale by running N worker
    # replicas (D4). Capacity = number of workers.
    max_jobs = 1
    # Backstop above the in-task hard kill so the in-task timeout fires first.
    job_timeout = settings.hard_budget_s + 30
    # A cancelled/interrupted job (worker redeploy, job_timeout backstop) is
    # re-enqueued by arq; cap it at ONE retry — jobs survive a redeploy, but a
    # pathological job can't be solved 5x (arq's default max_tries). The first
    # attempt's spawn subprocess dies at its own hard kill, so the transient
    # overlap is bounded. (Hardening round 2, A3-7.)
    max_tries = 2
    keep_result = settings.result_ttl_s
    keep_result_forever = False
