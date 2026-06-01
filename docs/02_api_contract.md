# 02 — API Contract

The HTTP surface (`schemas/` + `api/routes.py` are the implementation). All
endpoints are under `/api/v1`. Content type `application/json`. No auth.

> Maps onto the core's `Box` / `Pallet` / `PackerConfig` inputs and `to_json()`
> output. The service is a thin translation layer over those.

> **Implementation status (v0.1.0).** Live and matching this doc: `POST /pack`
> (`202` queued / `400 invalid_input`), `GET /jobs/{id}`
> (`queued`→`running`→`done`/`failed`/`timeout`, `404` when expired), `GET /health`,
> `GET /version` (`{service, core, api}`). **Deviations to know:** the `meta` block
> shown below is **reserved and not yet populated** (clients must not depend on it
> in v1); over-cap requests currently surface through the gate as `400 invalid_input`
> (a `problems` entry), **not** a distinct `413`; and the `503` queue-saturation
> response is **not implemented yet**. Also: **unlimited caps** (a `pallet.max_weight`
> or box `max_load_on_top` omitted or `null`) appear as **`null`** in the result —
> the service normalises the core's internal `inf`/`nan` so the JSON is strict
> (`allow_nan=false`) safe. These are tracked follow-ups, not contract changes.

---

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/pack` | Validate + enqueue a packing job |
| `GET` | `/api/v1/jobs/{job_id}` | Poll job status / fetch result |
| `GET` | `/api/v1/health` | Liveness/readiness (Redis reachable) |
| `GET` | `/api/v1/version` | Service + core-algorithm version |

---

## `POST /api/v1/pack`

Validates the request against the input contract (the core gate). If valid,
creates a job and returns immediately — it does **not** solve inline.

### Request body

```jsonc
{
  "boxes": [
    {
      "id": "B0001",          // required, unique string (used to map results)
      "length": 300,           // required, POSITIVE INTEGER (any unit, consistent)
      "width": 200,            // required, POSITIVE INTEGER
      "height": 150,           // required, POSITIVE INTEGER
      "weight": 2.5,           // optional float >= 0 (default 0)
      "max_load_on_top": 20.0, // optional float >= 0 or null=unlimited (fragile = 0)
      "rotations": "this_side_up", // "all" | "this_side_up" | "none" (default "all")
      "group": "CUST1",        // optional; same group -> same pallet
      "requires_full_support": false // optional
    }
    // ... up to 500 boxes
  ],
  "pallet": {
    "length": 1200,            // POSITIVE INTEGER
    "width": 1000,             // POSITIVE INTEGER
    "height": 1500,            // POSITIVE INTEGER
    "max_weight": 900,         // optional float > 0 or null=unlimited
    "max_overhang": 0          // optional non-negative INTEGER (default 0)
  },
  "options": {
    "max_pallets": 10,         // optional int >= 1 (default 1 = single-container)
    "time_budget_s": 90,       // optional; clamped to [min, service_max]
    "support_ratio": 0.8,      // optional [0,1] (default 0.8 = stability enforced)
    "seed": 42                 // optional int (omit -> service default, reproducible)
  }
}
```

**Spatial dimensions must be integers** (any unit — mm, cm, inch — applied
consistently). Weights/loads may be fractional. These rules are the core's input
gate; violations are rejected here, not silently coerced.

### Responses

`202 Accepted` — job queued:
```json
{
  "job_id": "pk_3f8a…",
  "status": "queued",
  "links": { "self": "/api/v1/jobs/pk_3f8a…" }
}
```

`400 Bad Request` — failed validation (the gate). Nothing is queued:
```json
{
  "error": {
    "code": "invalid_input",
    "message": "Request did not satisfy the packing input contract.",
    "problems": [
      "box[3] (id='B0003').length must be a positive integer (got 100.49)",
      "pallet.max_weight must be a positive number or infinity (got NaN)"
    ]
  }
}
```

`413 Payload Too Large` — over the box cap:
```json
{ "error": { "code": "too_many_boxes", "message": "601 boxes exceeds the limit of 500." } }
```

`429 Too Many Requests` — rate limited (per-IP). `503` — queue saturated.

---

## `GET /api/v1/jobs/{job_id}`

Poll a job. Status progression: `queued` → `running` → terminal
(`done` | `failed` | `timeout`).

`200 OK` while pending:
```json
{ "job_id": "pk_3f8a…", "status": "running",
  "meta": { "submitted_at": "…", "started_at": "…" } }
```

`200 OK` when done — `result` is the core's `to_json()` output:
```json
{
  "job_id": "pk_3f8a…",
  "status": "done",
  "meta": { "runtime_s": 71.8, "seed": 42, "expires_at": "…" },
  "result": {
    "input_summary": {
      "items_packed": 500, "items_unpacked": 0,
      "pallets_used": 24, "total_volume_utilisation": 0.71
    },
    "pallets": [
      {
        "pallet_id": "P001",
        "dimensions": { "L": 1200, "W": 1000, "H": 1500, "max_weight": 900 },
        "utilisation": 0.78,
        "cog": { "x": 600.1, "y": 500.4, "z": 410.2 },
        "total_weight": 612.0,
        "items": [
          {
            "item_id": "B0001",
            "position": { "x": 0, "y": 0, "z": 0 },
            "dimensions": { "L": 300, "W": 200, "H": 150 },
            "orientation": { "perm": [0,1,2], "name": "LWH" },
            "weight": 2.5,
            "support_ratio": 1.0,
            "supported_by": ["floor"],
            "supports": ["B0007"]
          }
        ]
      }
    ],
    "unpacked_items": [
      { "item_id": "B0500",
        "dimensions": { "L": 400, "W": 300, "H": 300 },
        "weight": 3.0, "reason": "no_feasible_placement" }
    ]
  }
}
```

`200 OK` on failure / timeout:
```json
{ "job_id": "pk_3f8a…", "status": "timeout",
  "error": { "code": "solve_timeout", "message": "Exceeded the 120s wall-clock limit." } }
```

`404 Not Found` — unknown or expired `job_id` (results are ephemeral, TTL-expired;
no history by design).

---

## `GET /api/v1/health`

`200` `{ "status": "ok", "redis": "ok" }` when Redis is reachable, else `503`.

## `GET /api/v1/version`

`core` is the configured `PALLET_API_CORE_VERSION` — the pinned git ref the core
was installed from in prod, or `"dev-mount"` when the core is mounted in dev (the
core package exposes no `__version__`).

```json
{ "service": "0.1.0", "core": "dev-mount", "api": "v1" }
```

---

## Notes / open questions (resolve during the build)

- **Polling vs. push.** MVP is polling. A `Retry-After`/suggested-interval hint
  in the queued/running responses is cheap and helpful. Server-sent events or
  websockets are a possible later enhancement, not MVP.
- **Result size.** A 500-box result is ~120 KB JSON. Acceptable inline. If it
  grows, consider gzip (it compresses heavily) — already standard via the ASGI
  server.
- **`rotations` shape.** Presets (`all`/`this_side_up`/`none`) cover the common
  cases; a raw list of orientation names can be added if a user needs finer
  control.
- **Seed exposure.** Default to a fixed service seed (reproducible). Allowing a
  caller seed is harmless and aids reproducibility/debugging.
- **CoG envelope / advanced constraints.** `support_ratio` is exposed in `options`
  for the MVP; the core also supports a CoG envelope and centroid rules. Decide
  per-field which advanced constraints to surface in v1 vs. keep at sensible
  defaults.
