"""End-to-end test against a LIVE stack (submit -> poll -> done). Pure urllib —
no core import needed. Skips unless a stack answers on the base URL, so it runs
on the host after `docker compose up` (or in CI). Override with PALLET_API_E2E_BASE.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

import pytest

BASE = os.getenv("PALLET_API_E2E_BASE", "http://localhost:8000/api/v1")


def _alive() -> bool:
    try:
        urllib.request.urlopen(BASE + "/health", timeout=2)
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(not _alive(), reason=f"no live stack at {BASE}")


def _post(path: str, body: dict):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


def _get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


def test_submit_poll_done():
    body = {
        "boxes": [{"id": f"B{i}", "length": 300, "width": 200, "height": 150,
                   "weight": 2.0} for i in range(5)],
        "pallet": {"length": 1200, "width": 1000, "height": 1500},
        "options": {"max_pallets": 1, "time_budget_s": 10},
    }
    st, j = _post("/pack", body)
    assert st == 202
    jid = j["job_id"]

    payload, status = {}, None
    deadline = time.time() + 120
    while time.time() < deadline:
        _, payload = _get(f"/jobs/{jid}")
        status = payload["status"]
        if status in ("done", "failed", "timeout"):
            break
        time.sleep(1)

    assert status == "done", f"terminal status was {status}: {payload.get('error')}"
    res = payload["result"]
    summ = res["input_summary"]
    assert summ["items_packed"] + summ["items_unpacked"] == 5
    # the full result schema survives response_model coercion (placement intact)
    item = res["pallets"][0]["items"][0]
    assert {"item_id", "position", "dimensions"} <= set(item)
    assert {"x", "y", "z"} <= set(item["position"])


def test_duplicate_id_is_400():
    # Well-shaped but breaks the contract (duplicate id) -> the gate returns 400.
    body = {
        "boxes": [{"id": "X", "length": 300, "width": 200, "height": 150},
                  {"id": "X", "length": 300, "width": 200, "height": 150}],
        "pallet": {"length": 1200, "width": 1000, "height": 1500},
        "options": {},
    }
    with pytest.raises(urllib.error.HTTPError) as ei:
        _post("/pack", body)
    assert ei.value.code == 400
