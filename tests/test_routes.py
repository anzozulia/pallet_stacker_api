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


def test_duplicate_id_is_400(client, pack_payload):
    # Duplicate ids are well-shaped but break the contract -> the gate returns 400.
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


def test_unknown_job_is_404_with_envelope(client):
    r = client.get(f"{V1}/jobs/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"      # uniform error envelope


def test_schema_violation_is_422_with_envelope(client):
    # A box missing the required `id` is a schema (not contract) error -> 422,
    # wrapped in the same {"error": {...}} envelope as every other error.
    body = {"boxes": [{"length": 300, "width": 200, "height": 150}],
            "pallet": {"length": 1200, "width": 1000, "height": 1500}}
    r = client.post(f"{V1}/pack", json=body)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert err["problems"]


def test_out_of_range_option_is_422(client, pack_payload):
    # support_ratio out of [0,1] is a schema-bound violation -> 422 (not gate 400).
    body = pack_payload(2, budget=5)
    body["options"]["support_ratio"] = 2.0
    r = client.post(f"{V1}/pack", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_non_integer_dimension_is_422(client):
    # Spatial dims are typed `int`, so a non-integer is a schema violation -> 422.
    body = {"boxes": [{"id": "B1", "length": 300.5, "width": 200, "height": 150}],
            "pallet": {"length": 1200, "width": 1000, "height": 1500}}
    r = client.post(f"{V1}/pack", json=body)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"


def test_root_redirects_to_docs(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (301, 302, 307, 308)
    assert r.headers["location"].endswith("/docs")


def test_oversized_body_is_413_envelope(client, monkeypatch):
    # C2/F6: honest Content-Length over the cap -> rejected before parsing.
    monkeypatch.setattr(settings, "max_body_bytes", 1024)
    r = client.post(f"{V1}/pack", content=b"x" * 2048,
                    headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


def test_oversized_chunked_body_is_413(client, monkeypatch):
    # No Content-Length (chunked): the middleware counts the received bytes.
    monkeypatch.setattr(settings, "max_body_bytes", 1024)
    r = client.post(f"{V1}/pack", content=iter([b"x" * 512] * 4),
                    headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert r.json()["error"]["code"] == "payload_too_large"


def test_body_under_cap_still_parses(client, pack_payload, monkeypatch):
    # The cap must not eat legitimate requests (the 422 proves the body
    # reached pydantic intact).
    monkeypatch.setattr(settings, "max_body_bytes", 1024)
    r = client.post(f"{V1}/pack", json={"boxes": []})
    assert r.status_code == 422


def test_rate_limiter_redis_failure_is_503_envelope():
    # C8/F13: a Redis error inside the limiter must surface as the same
    # degraded 503 envelope as /pack's own queue path, not a bare 500.
    import asyncio
    import types

    from fastapi import HTTPException
    from redis.exceptions import RedisError

    from pallet_api.api.limits import enforce_rate_limit

    class _DeadRedis:
        async def incr(self, key):
            raise RedisError("connection refused")

        async def expire(self, key, ttl):
            raise RedisError("connection refused")

    req = types.SimpleNamespace(
        app=types.SimpleNamespace(state=types.SimpleNamespace(redis=_DeadRedis())),
        client=types.SimpleNamespace(host="1.2.3.4"))
    with pytest.raises(HTTPException) as ei:
        asyncio.run(enforce_rate_limit(req))
    assert ei.value.status_code == 503
    assert ei.value.detail["error"]["code"] == "degraded"


def test_rate_limit_returns_429(client, pack_payload, monkeypatch, flush_redis):
    # Low limit + a flushed db so the minute-window counter starts clean. Bad
    # payloads keep the test side-effect-free: the limiter (a dependency) runs
    # and increments BEFORE the handler validates, so over-limit -> 429.
    monkeypatch.setattr(settings, "rate_limit_per_min", 3)
    resps = [client.post(f"{V1}/pack", json=pack_payload(2, bad=True)) for _ in range(5)]
    codes = [r.status_code for r in resps]
    assert codes[-1] == 429
    assert 429 not in codes[:3]                # first 3 within the limit
    assert resps[-1].json()["error"]["code"] == "rate_limited"   # uniform envelope
