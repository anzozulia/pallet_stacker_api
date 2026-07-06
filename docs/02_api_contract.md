# 02 — API Contract

The HTTP surface (`schemas/` + `api/routes.py` are the implementation). All
endpoints are under `/api/v1`. Content type `application/json`. No auth.

> **Live, authoritative reference:** run the service and open
> **`/docs`** (Swagger UI) or **`/openapi.json`**. The app is fully annotated
> (per-field descriptions, examples, every response code, the error envelope), so
> the generated docs are self-sufficient — this file is the design rationale; the
> OpenAPI schema is the contract.

> Maps onto the core's `Box` / `Pallet` / `PackerConfig` inputs and `to_json()`
> output. The service is a thin translation layer over those.

> **Implementation status (v0.1.0).** Live and matching this doc: `POST /pack`
> (`202` queued / `400 invalid_input`), `GET /jobs/{id}`
> (`queued`→`running`→`done`/`failed`/`timeout`, `404` when expired), `GET /health`,
> `GET /version` (`{service, core, api}`). **Deviations to know:** the `meta` block
> shown below is **reserved and not yet populated** (clients must not depend on it
> in v1); over-cap requests (too many BOXES) surface through the gate as
> `400 invalid_input` (a `problems` entry) — the `413` is reserved for request
> BODIES over the byte cap (default 10 MB, `payload_too_large`); and the `503`
> queue-saturation response is **not implemented yet** (a Redis outage does
> return the `503 degraded` envelope, from `/pack`, `/jobs`, and the rate
> limiter alike). Validation is split: schema violations
> (wrong type, **non-integer dimension**, out-of-bounds) return `422
> validation_error`; only duplicate ids and over-cap return `400 invalid_input`. Also: **unlimited caps** (a `pallet.max_weight`
> or box `max_load_on_top` omitted or `null`) are **omitted** from the result —
> the service normalises the core's internal `inf`/`nan` (so the JSON is strict
> `allow_nan=false` safe) and drops null optional fields. These are tracked
> follow-ups, not contract changes.

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
      "id": "B0001",          // required, unique string, 1-128 chars (maps results)
      "length": 300,           // required, POSITIVE INTEGER <= 1e6 (any unit, consistent)
      "width": 200,            // required, POSITIVE INTEGER <= 1e6
      "height": 150,           // required, POSITIVE INTEGER <= 1e6
      "weight": 2.5,           // optional float in [0, 1e12] (default 0; finite)
      "max_load_on_top": 20.0, // optional float in [0, 1e12] or null=unlimited (fragile = 0)
      "rotations": "this_side_up", // "all" | "this_side_up" | "none" (default "all")
      "group": "CUST1",        // optional, <=128 chars; same group -> same pallet
                               //   (""/whitespace = no group). A group larger
                               //   than one pallet is NEVER split: the
                               //   remainder is returned unpacked even when
                               //   more pallets are allowed.
      "requires_full_support": false // optional
    }
    // ... up to 500 boxes
  ],
  "pallet": {
    "length": 1200,            // POSITIVE INTEGER
    "width": 1000,             // POSITIVE INTEGER
    "height": 1500,            // POSITIVE INTEGER
    "max_weight": 900,         // optional float in (0, 1e12] or null=unlimited
    "max_overhang": 0          // optional non-negative INTEGER, <= min(length, width).
                               //   With overhang > 0 the whole-pallet weighted
                               //   CoG is constrained to the deck footprint
                               //   [0,L]x[0,W] (round 7, ADR D20), AND each
                               //   rigid sub-assembly's CoG must project over
                               //   its own deck contact (round 8, ADR D21): a
                               //   load that could only pack by putting its
                               //   balance — or a detached stack's balance —
                               //   past the deck edge (it would tip) is packed
                               //   balanced or returned unpacked.
  },
  "options": {
    "max_pallets": 10,         // optional int 1..100 (default 1 = single-container).
                               //   Multi-pallet solves optimise unpacked volume
                               //   first, then pallet count, then layout realism
                               //   (round 6, ADR D19).
    "time_budget_s": 90,       // optional; clamped to [min, service_max]
    "support_ratio": 0.8,      // optional [0,1] (default 0.8 = stability enforced).
                               //   Below 0.5 with max_overhang > 0, floor
                               //   placements are additionally held to the
                               //   toppling rule (centroid over the deck
                               //   contact, round 6 / ADR D18) — a box that
                               //   could only sit tipping past the deck edge
                               //   is returned unpacked instead.
    "seed": 42                 // optional int >= 0 (omit -> service default, reproducible)
  }
}
```

**Spatial dimensions must be integers** (any unit — mm, cm, inch — applied
consistently). Weights/loads may be fractional. These rules are the core's input
gate; violations are rejected here, not silently coerced.

**Modeling fragile, orientation-sensitive goods:** `max_load_on_top` caps the
stacked **weight** only — it does not stop the solver tipping the box on its
side to fit a gap. Goods that must also stay upright (glassware, "this way up"
cartons) need **both** `max_load_on_top: 0` **and** `rotations: "this_side_up"`.

**Seed determinism nuance:** same input + same `seed` ⇒ identical plan, always.
The converse is not promised — on highly symmetric loads (e.g. all-identical
boxes) *different* seeds can legitimately return identical plans, because every
dense flat layout ties on both volume and the realism terms.

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

`413 Payload Too Large` — request **body** over the byte cap (default 10 MB,
`PALLET_API_MAX_BODY_BYTES`; a legitimate max-size request is a few hundred KB).
Too many *boxes* is a `400` from the gate, not a `413`:
```json
{ "error": { "code": "payload_too_large", "message": "Request body exceeds the 10 MB limit." } }
```

`422 Unprocessable Entity` — schema violation, including **unknown fields**
(round 3): a typo'd field name (`"suport_ratio"`, `"time_budget"`) is
rejected with the field named in `problems`, never silently ignored — a
misspelled option used to mean solving with defaults and no signal. Clients
must send exactly the documented fields.

`429 Too Many Requests` — rate limited (per-IP). Since round 5 the limiter
runs BEFORE the request body is received (an over-limit client cannot make
the server read or parse 10 MB bodies, and oversize attempts count toward
the limit), and result polling has its own generous per-IP cap
(`PALLET_API_POLL_RATE_LIMIT_PER_MIN`, default 600/min on `GET /jobs/*`;
`/health`, `/version` and `/docs` are never limited). CORS for browser
front-ends is opt-in via `PALLET_API_CORS_ORIGINS` (absent by default —
server-side callers need nothing). Misconfigured deployments fail fast: a
zero/negative/inverted budget or limit env refuses to boot instead of
serving a silently dead API. `503 degraded` — Redis (queue /
result store) unreachable; returned by `/pack`, `/jobs/{id}`, and the rate
limiter alike.

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
            "support_ratio": 1.0,   // typically [0,1]; may exceed 1.0 ONLY on
                                    //   plans served with `warnings` (degraded
                                    //   geometry double-counts overlapping
                                    //   supporters — honest, not clamped)
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

**Physical validity guarantee (round 3, ADR D17).** Every `done` result has
passed replay validation (geometry, support, deck contact, and the load
model). In the rare case the solver emits a plan that fails validation, the
service deterministically **repairs** it before serving: the boxes feeding
the overload are moved to `unpacked_items` with `reason:
"load_limit_repair"`, and a `warnings` array on `result` lists the original
violations plus the repair actions. `warnings` is absent on clean solves —
its presence indicates an engine defect worth reporting, not a caller error.

Two result-shape notes: `unpacked_items[].reason` is `no_feasible_placement`
(the solver found no legal spot) or `load_limit_repair` (stripped by the
post-solve repair); `utilisation` / `total_volume_utilisation` divide by the
**raw deck** volume, so with `max_overhang > 0` a legally-overhanging load
can report **more than 1.0** — that is honest bookkeeping, not an error.

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

`core` is the configured `PALLET_API_CORE_VERSION` — the version of the vendored
core (see `core/VENDOR.md`; the core package exposes no `__version__`).

```json
{ "service": "0.1.0", "core": "3.13.0+fd2ea2c", "api": "v1" }
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
- **Small-instance budget floor (round 3).** Requests with ≤ 60 boxes get
  their effective budget floored at **2 s** regardless of a smaller
  `time_budget_s`: the exact-fit warm start is silently skipped below 2 s
  (a perfect 2×2×2 tiling packed 7/8 at 1.9 s), and small instances finish
  well under the floor anyway. Group-heavy multi-pallet solves split the
  budget per pallet and can still lose the warm start — give grouped
  requests generous budgets.
- **CoG envelope / advanced constraints.** `support_ratio` is exposed in `options`
  for the MVP; the core also supports a CoG envelope and centroid rules. Decide
  per-field which advanced constraints to surface in v1 vs. keep at sensible
  defaults.
