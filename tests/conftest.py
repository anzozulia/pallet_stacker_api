"""Shared test fixtures + path setup.

The suite is two-tier (see tests/README.md):
  * host tier  — schemas + loadtest helpers; need neither the core nor Redis.
  * docker tier — adapter/runner/worker/routes; guarded by
    `pytest.importorskip("pallet_packer")` in each module, so on a host without
    the core they skip cleanly instead of erroring at collection.
Redis-dependent tests additionally use the `redis_ready` fixture, which skips
when no Redis is reachable.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

# Make the service package + the scripts dir importable without installing.
_ROOT = pathlib.Path(__file__).resolve().parents[1]
for _p in (_ROOT / "src", _ROOT / "scripts", _ROOT):
    _s = str(_p)
    if _s not in sys.path:
        sys.path.insert(0, _s)

from pallet_api.config import settings  # noqa: E402  (pure module, host-safe)


def _redis_alive(url: str) -> bool:
    try:
        import redis  # runtime dep; absent on a bare host
        c = redis.from_url(url, socket_connect_timeout=1)
        try:
            c.ping()
            return True
        finally:
            c.close()
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture(scope="session")
def redis_url() -> str:
    return settings.redis_url


@pytest.fixture
def redis_ready(redis_url: str) -> str:
    if not _redis_alive(redis_url):
        pytest.skip(f"no Redis reachable at {redis_url}")
    return redis_url


@pytest.fixture
def flush_redis(redis_ready: str):
    """A clean Redis db for the test (used by the rate-limit test so prior
    requests in the same minute-window don't pollute the counter)."""
    import redis
    c = redis.from_url(redis_ready)
    c.flushdb()
    yield c
    c.flushdb()
    c.close()


@pytest.fixture
def fast_cfg() -> dict:
    """A small solver config so real solves finish in ~seconds, not the full
    budget. Same shape as settings.solver_cfg()."""
    return {
        "max_boxes": 500,
        "soft_budget_s": 10.0,
        "default_max_pallets": 1,
        "default_seed": 42,
        "population_size": 30,
        "n_populations": 2,
        "patience": 8,
        "n_modes": 6,
    }


@pytest.fixture
def pack_payload():
    """Factory for a valid /pack request dict. `bad=True` gives all boxes the same
    id — a contract violation only the core gate catches (a 400 / PackingInputError),
    as opposed to a schema violation (422)."""
    def _make(n_boxes: int = 3, *, max_pallets: int = 1, budget=None,
              seed=None, bad: bool = False) -> dict:
        boxes = [{
            "id": ("DUP" if bad else f"B{i:03d}"),
            "length": 300, "width": 200, "height": 150,
            "weight": 2.0,
        } for i in range(n_boxes)]
        opts: dict = {"max_pallets": max_pallets}
        if budget is not None:
            opts["time_budget_s"] = budget
        if seed is not None:
            opts["seed"] = seed
        return {
            "boxes": boxes,
            "pallet": {"length": 1200, "width": 1000, "height": 1500},
            "options": opts,
        }
    return _make
