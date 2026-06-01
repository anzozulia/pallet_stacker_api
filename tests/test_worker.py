"""arq worker-task test: solve_job wires settings.solver_cfg() -> runner and
returns the status dict arq stores. Needs the core."""
from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("pallet_packer")

from pallet_api.config import settings           # noqa: E402
from pallet_api.workers.tasks import solve_job    # noqa: E402


def test_solve_job_returns_done(monkeypatch, pack_payload):
    # Shrink the solver so the task finishes in seconds, not the full budget.
    monkeypatch.setattr(settings, "population_size", 30)
    monkeypatch.setattr(settings, "n_populations", 2)
    monkeypatch.setattr(settings, "patience", 8)
    monkeypatch.setattr(settings, "soft_budget_s", 10.0)

    out = asyncio.run(solve_job({"job_id": "test"}, pack_payload(4, budget=5)))
    assert out["status"] == "done"
    assert "input_summary" in out["result"]
