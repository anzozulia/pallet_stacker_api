"""Round-5 ops hardening (R2/R3/R4/R6): fail-fast settings, responsive
crash detection, pre-body rate limiting + poll limiter, env-gated CORS,
bare-env-var semantics."""
from __future__ import annotations

import importlib
import os
import time

import pytest
from fastapi.testclient import TestClient


# ------------------------------------------------ R2: fail-fast settings
def _reload_settings():
    import sys
    import pallet_api.config.settings  # noqa: F401 — ensure it's loaded
    # NB: the package re-exports the `settings` INSTANCE under the same
    # name, shadowing the submodule attribute — reload via sys.modules.
    return importlib.reload(sys.modules["pallet_api.config.settings"])


def test_bad_budget_envs_refuse_to_boot(monkeypatch):
    try:
        monkeypatch.setenv("PALLET_API_HARD_BUDGET_S", "0")
        with pytest.raises(RuntimeError, match="SOFT"):
            _reload_settings()
        monkeypatch.setenv("PALLET_API_HARD_BUDGET_S", "30")   # < soft 90
        with pytest.raises(RuntimeError, match="HARD"):
            _reload_settings()
        monkeypatch.setenv("PALLET_API_HARD_BUDGET_S", "120")
        monkeypatch.setenv("PALLET_API_RATE_LIMIT_PER_MIN", "0")
        with pytest.raises(RuntimeError, match="RATE_LIMIT"):
            _reload_settings()
    finally:
        for k in ("PALLET_API_HARD_BUDGET_S", "PALLET_API_RATE_LIMIT_PER_MIN"):
            monkeypatch.delenv(k, raising=False)
        _reload_settings()          # restore a clean module state


# ------------------------------------------------ R6: bare env var = default
def test_bare_env_var_means_default(monkeypatch):
    from pallet_api.config.settings import _b
    monkeypatch.setenv("X_TEST_FLAG", "")
    assert _b("X_TEST_FLAG", True) is True      # was: silently False
    assert _b("X_TEST_FLAG", False) is False
    monkeypatch.setenv("X_TEST_FLAG", "0")
    assert _b("X_TEST_FLAG", True) is False
    monkeypatch.delenv("X_TEST_FLAG")
    assert _b("X_TEST_FLAG", True) is True


# ------------------------------------------------ R3: crash detected fast
def _crash_target(payload, cfg, q):   # top-level → picklable for spawn
    os._exit(1)


def test_crashed_child_reported_quickly():
    from pallet_api.solver.runner import run_with_hard_timeout
    t0 = time.monotonic()
    out = run_with_hard_timeout({}, {}, hard_timeout_s=30,
                                _target_fn=_crash_target)
    wall = time.monotonic() - t0
    assert out["status"] == "failed"
    assert out["error"]["code"] == "solver_crashed"
    assert wall < 5, f"crash took {wall:.1f}s to surface (was ~hard budget)"


# ------------------------------------------------ R4: middleware behavior
@pytest.fixture
def client(redis_ready):
    from pallet_api.api.app import create_app
    with TestClient(create_app()) as c:
        yield c


def test_poll_limiter_429(client, flush_redis, monkeypatch):
    from pallet_api.config import settings
    monkeypatch.setattr(settings, "poll_rate_limit_per_min", 2)
    codes = [client.get("/api/v1/jobs/pk_nonexistent").status_code
             for _ in range(3)]
    assert codes[0] == 404 and codes[1] == 404
    assert codes[2] == 429
    body = client.get("/api/v1/jobs/pk_nonexistent").json()
    assert body["error"]["code"] == "rate_limited"


def test_rate_limit_precedes_body_receipt(client, flush_redis, monkeypatch,
                                          pack_payload):
    """A rate-limited IP gets 429 even on an oversize body — proof the
    limiter runs before the body-cap middleware ever streams bytes."""
    from pallet_api.config import settings
    monkeypatch.setattr(settings, "rate_limit_per_min", 1)
    assert client.post("/api/v1/pack", json=pack_payload(2)).status_code == 202
    huge = b"x" * (settings.max_body_bytes + 100)
    r = client.post("/api/v1/pack", content=huge,
                    headers={"content-type": "application/json"})
    assert r.status_code == 429          # not 413
    assert r.json()["error"]["code"] == "rate_limited"


def test_health_and_version_never_limited(client, flush_redis, monkeypatch):
    from pallet_api.config import settings
    monkeypatch.setattr(settings, "poll_rate_limit_per_min", 1)
    monkeypatch.setattr(settings, "rate_limit_per_min", 1)
    for _ in range(5):
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/version").status_code == 200


def test_cors_absent_by_default_and_present_when_configured(redis_ready,
                                                            monkeypatch):
    from pallet_api.config import settings
    from pallet_api.api.app import create_app
    with TestClient(create_app()) as c:
        r = c.options("/api/v1/pack",
                      headers={"Origin": "https://app.example.com",
                               "Access-Control-Request-Method": "POST"})
        assert "access-control-allow-origin" not in r.headers
    monkeypatch.setattr(settings, "cors_origins", "https://app.example.com")
    with TestClient(create_app()) as c:
        r = c.options("/api/v1/pack",
                      headers={"Origin": "https://app.example.com",
                               "Access-Control-Request-Method": "POST"})
        assert r.status_code == 200
        assert (r.headers.get("access-control-allow-origin")
                == "https://app.example.com")
