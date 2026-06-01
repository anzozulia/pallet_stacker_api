"""HTTP contract tests via FastAPI TestClient against the real app + real Redis
(no worker — the done/result path is covered by test_e2e). Needs core + Redis."""
from __future__ import annotations

import pytest

pytest.importorskip("pallet_packer")
pytest.importorskip("httpx")          # TestClient transport

from fastapi.testclient import TestClient   # noqa: E402

from pallet_api.config import settings       # noqa: E402

V1 = "/api/v1"


@pytest.fixture
def client(redis_ready):
    from pallet_api.api.app import app
    with TestClient(app) as c:        # runs lifespan -> connects the arq pool
        yield c


def test_health_ok(client):
    r = client.get(f"{V1}/health")
    assert r.status_code == 200
    assert r.json()["redis"] == "ok"


def test_version_has_core_field(client):
    j = client.get(f"{V1}/version").json()
    assert set(j) >= {"service", "core", "api"}


def test_pack_accepts_and_queues(client, pack_payload):
    r = client.post(f"{V1}/pack", json=pack_payload(3, budget=5))
    assert r.status_code == 202
    j = r.json()
    assert j["status"] == "queued"
    assert j["job_id"]
    assert j["links"]["self"].endswith(j["job_id"])


def test_pack_malformed_is_400(client, pack_payload):
    r = client.post(f"{V1}/pack", json=pack_payload(2, bad=True))
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "invalid_input"
    assert err["problems"]                     # actionable problem list


def test_pack_over_cap_is_400(client, pack_payload, monkeypatch):
    monkeypatch.setattr(settings, "max_boxes", 2)
    r = client.post(f"{V1}/pack", json=pack_payload(3, budget=5))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_input"


def test_unknown_job_is_404(client):
    r = client.get(f"{V1}/jobs/does-not-exist")
    assert r.status_code == 404


def test_rate_limit_returns_429(client, pack_payload, monkeypatch, flush_redis):
    # Low limit + a flushed db so the minute-window counter starts clean. Bad
    # payloads keep the test side-effect-free: the limiter (a dependency) runs
    # and increments BEFORE the handler validates, so over-limit -> 429.
    monkeypatch.setattr(settings, "rate_limit_per_min", 3)
    codes = [client.post(f"{V1}/pack", json=pack_payload(2, bad=True)).status_code
             for _ in range(5)]
    assert codes[-1] == 429
    assert 429 not in codes[:3]                # first 3 within the limit
