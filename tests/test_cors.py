"""CORS contract tests — the headers that let the static browser front-end call
this API cross-origin. Without them the browser blocks every request and the UI
shows "Couldn't reach the packing service".

Host-runnable: CORS preflight (OPTIONS) is answered by the middleware BEFORE any
route runs, so these tests need neither the vendored core nor Redis. The non-OPTIONS
assertions hit `/api/v1/version`, which also touches no Redis. The TestClient is used
WITHOUT its context manager so the app lifespan (the arq/Redis pool) never starts.
"""
from __future__ import annotations

import pytest

pytest.importorskip("httpx")          # TestClient transport

from fastapi.testclient import TestClient   # noqa: E402

from pallet_api.api.app import app, create_app   # noqa: E402
from pallet_api.config import settings           # noqa: E402

V1 = "/api/v1"
ORIGIN = "https://app.example.com"


def test_preflight_pack_allows_cross_origin_post():
    """The POST /pack preflight (sent by the browser because the request is JSON)
    must succeed and echo Access-Control-Allow-Origin — the exact hop that was
    silently failing and producing the 'unreachable' error."""
    client = TestClient(app)        # no `with`: do not start the Redis lifespan
    r = client.options(
        f"{V1}/pack",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] in (ORIGIN, "*")
    assert "POST" in r.headers["access-control-allow-methods"]


def test_simple_get_carries_allow_origin():
    """A simple GET (the poll request) must carry Access-Control-Allow-Origin so the
    browser lets the page read the response body."""
    client = TestClient(app)
    r = client.get(f"{V1}/version", headers={"Origin": ORIGIN})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] in (ORIGIN, "*")


def test_origins_can_be_locked_down(monkeypatch):
    """With an explicit allow-list, only listed origins are echoed; others get no
    Access-Control-Allow-Origin (the browser then blocks them)."""
    monkeypatch.setattr(settings, "cors_origins", [ORIGIN])
    locked = TestClient(create_app())   # reads settings.cors_origins at build time

    allowed = locked.get(f"{V1}/version", headers={"Origin": ORIGIN})
    assert allowed.headers.get("access-control-allow-origin") == ORIGIN

    denied = locked.get(f"{V1}/version", headers={"Origin": "https://evil.example.com"})
    assert "access-control-allow-origin" not in denied.headers
