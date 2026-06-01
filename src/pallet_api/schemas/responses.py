"""Response schemas. Every endpoint — including the packing **result** — is fully
typed so the OpenAPI schema (and `/docs`) is a complete, self-service reference.

The result models mirror the core's `to_json()` output exactly. They are
deliberately lenient (`extra="allow"`, all fields optional) so the service never
drops or rejects a field the core adds — the docs describe the known shape while
the wire payload always passes through verbatim. Unlimited caps (e.g.
`max_weight`) are normalised to omitted (see the adapter's `_json_safe`)."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

_LENIENT = {"extra": "allow"}


# --------------------------------------------------------------- operational

class HealthState(BaseModel):
    """Liveness/readiness. `GET /health`."""
    status: str = Field(..., description="`ok` when the service can serve requests.",
                        examples=["ok"])
    redis: str = Field(..., description="`ok` when Redis (the queue + result store) "
                       "is reachable; otherwise the endpoint returns 503.",
                       examples=["ok"])
    model_config = {"json_schema_extra": {"examples": [{"status": "ok", "redis": "ok"}]}}


class VersionInfo(BaseModel):
    """Service + core versions. `GET /version`."""
    service: str = Field(..., description="This API service's version.",
                         examples=["0.1.0"])
    core: str = Field(..., description="The vendored `pallet_packer` core version "
                      "(see core/VENDOR.md).", examples=["3.13.0+fd2ea2c"])
    api: str = Field(..., description="API contract version (URL prefix `/api/v1`).",
                     examples=["v1"])
    model_config = {"json_schema_extra": {"examples": [
        {"service": "0.1.0", "core": "3.13.0+fd2ea2c", "api": "v1"}]}}


# --------------------------------------------------------------- accept + error

class JobAccepted(BaseModel):
    """Returned by `POST /pack` when a job is validated and queued (HTTP 202).
    The solve runs asynchronously — poll `links.self` for the result."""
    job_id: str = Field(..., description="Opaque job id — the only handle to the job; "
                        "there is no other history.",
                        examples=["pk_3f8a1c9e4b7d4a2e9c1f"])
    status: Literal["queued"] = Field("queued",
                                       description="Always `queued` for a fresh job.")
    links: Dict[str, str] = Field(..., description="Hypermedia links. Currently only "
                                  "`self` — the poll URL for this job.",
                                  examples=[{"self": "/api/v1/jobs/pk_3f8a1c9e4b7d4a2e9c1f"}])
    model_config = {"json_schema_extra": {"examples": [{
        "job_id": "pk_3f8a1c9e4b7d4a2e9c1f", "status": "queued",
        "links": {"self": "/api/v1/jobs/pk_3f8a1c9e4b7d4a2e9c1f"}}]}}


class ErrorBody(BaseModel):
    """The body of an error (always inside an `error` envelope — see `ErrorEnvelope`)."""
    code: str = Field(..., description="Stable, machine-readable error code. As an HTTP "
                      "error: `validation_error` (422, body failed schema), "
                      "`invalid_input` (400, failed the packing contract/gate), "
                      "`not_found` (404, unknown/expired job), `rate_limited` (429), "
                      "`degraded` (503, Redis down). Inside a **`200` poll** with a "
                      "terminal `failed`/`timeout` status, `JobState.error.code` is one "
                      "of: `solve_timeout` (hit the hard wall-clock kill), "
                      "`solver_crashed` / `solver_error` / `solver_failed` (internal "
                      "failure), or `invalid_input` (the worker's backstop gate, with "
                      "`problems`). Not a fixed enum — treat unknown codes as a generic "
                      "error.", examples=["invalid_input"])
    message: Optional[str] = Field(None, description="Human-readable explanation. May "
                                   "be null when `problems` carries the detail.",
                                   examples=["Request did not satisfy the packing input contract."])
    problems: Optional[List[str]] = Field(None, description="Per-field violations, "
                                          "present for `invalid_input`/`validation_error`.",
                                          examples=[["box[3] (id='B0003').length must be "
                                                     "a positive integer (got 100.5)"]])


class ErrorEnvelope(BaseModel):
    """The uniform shape of **every** 4xx/5xx response from this API."""
    error: ErrorBody = Field(..., description="The error payload — `code` plus optional "
                             "`message`/`problems`.")
    model_config = {"json_schema_extra": {"examples": [{
        "error": {"code": "invalid_input",
                  "message": "Request did not satisfy the packing input contract.",
                  "problems": ["box[3] (id='B0003').length must be a positive integer (got 100.5)"]}}]}}


# --------------------------------------------------------------- packing result

class InputSummary(BaseModel):
    """Roll-up counts for the whole packing."""
    items_packed: Optional[int] = Field(None, description="Boxes successfully placed.")
    items_unpacked: Optional[int] = Field(None, description="Boxes that did not fit "
                                          "(see `unpacked_items`).")
    pallets_used: Optional[int] = Field(None, description="Number of pallets/containers used.")
    total_volume_utilisation: Optional[float] = Field(
        None, description="Packed volume ÷ total used-pallet volume, across all pallets [0,1].")
    model_config = _LENIENT


class Vec3(BaseModel):
    """A 3D point in the input unit. Axes: `x`=length, `y`=width, `z`=height (up)."""
    x: Optional[float] = Field(None, description="Along the length axis.")
    y: Optional[float] = Field(None, description="Along the width axis.")
    z: Optional[float] = Field(None, description="Along the height/up axis.")
    model_config = _LENIENT


class BoxDims(BaseModel):
    """Box extents along the placement axes (after rotation). `L`/`W`/`H` here
    correspond to the request's `length`/`width`/`height`, mapped onto x/y/z."""
    L: Optional[float] = Field(None, description="Extent along x — the request `length`.")
    W: Optional[float] = Field(None, description="Extent along y — the request `width`.")
    H: Optional[float] = Field(None, description="Extent along z — the request `height`.")
    model_config = _LENIENT


class PalletDims(BaseModel):
    """Pallet/container dimensions. `L`/`W`/`H` mirror the request `length`/`width`/
    `height`; `max_weight` omitted ⇒ unlimited."""
    L: Optional[float] = Field(None, description="Length (request `length`).")
    W: Optional[float] = Field(None, description="Width (request `width`).")
    H: Optional[float] = Field(None, description="Height (request `height`).")
    max_weight: Optional[float] = Field(None, description="Weight cap; omitted = unlimited.")
    model_config = _LENIENT


class Orientation(BaseModel):
    """The chosen rotation. `perm` maps the box's original (length, width, height)
    axes onto (x, y, z); `name` is the mnemonic (e.g. `LWH` = no rotation)."""
    perm: Optional[List[int]] = Field(None, description="Permutation of the box's "
                                      "(L,W,H) axes onto (x,y,z).", examples=[[0, 1, 2]])
    name: Optional[str] = Field(None, description="Mnemonic for the orientation.",
                                examples=["LWH"])
    model_config = _LENIENT


class PlacementOut(BaseModel):
    """One placed box."""
    item_id: Optional[str] = Field(None, description="The box `id` from the request.")
    position: Optional[Vec3] = Field(None, description="Coordinates of the box's "
                                     "**minimum corner**; it occupies "
                                     "[x, x+L]×[y, y+W]×[z, z+H].")
    dimensions: Optional[BoxDims] = Field(None, description="Placed extents along x/y/z.")
    orientation: Optional[Orientation] = Field(None, description="The chosen rotation.")
    weight: Optional[float] = Field(None, description="The box's weight (from the request).")
    support_ratio: Optional[float] = Field(None, description="Fraction of this box's "
                                           "base supported from below [0,1]; 1.0 = full.")
    supported_by: Optional[List[str]] = Field(None, description="Ids of items directly "
                                              "beneath this box, or `floor`.",
                                              examples=[["floor"]])
    supports: Optional[List[str]] = Field(None, description="Ids of items resting on "
                                          "top of this box.", examples=[["B16"]])
    model_config = _LENIENT


class PalletOut(BaseModel):
    """One pallet/container and the boxes placed on it."""
    pallet_id: Optional[str] = Field(None, description="Identifier of this pallet.",
                                     examples=["P001"])
    dimensions: Optional[PalletDims] = Field(None, description="This pallet's dimensions "
                                             "(and weight cap).")
    utilisation: Optional[float] = Field(None, description="Packed volume ÷ this "
                                         "pallet's volume [0,1].")
    cog: Optional[Vec3] = Field(None, description="Centre of gravity of the load.")
    total_weight: Optional[float] = Field(None, description="Total weight of the boxes "
                                          "on this pallet.")
    items: Optional[List[PlacementOut]] = Field(None, description="Placed boxes.")
    model_config = _LENIENT


class UnpackedItem(BaseModel):
    """A box that could not be placed."""
    item_id: Optional[str] = Field(None, description="The box `id` from the request.")
    dimensions: Optional[BoxDims] = Field(None, description="The box's dimensions.")
    weight: Optional[float] = Field(None, description="The box's weight.")
    reason: Optional[str] = Field(None, description="Why it was left out.",
                                  examples=["no_feasible_placement"])
    model_config = _LENIENT


class PackResult(BaseModel):
    """The packing plan — the value of `JobState.result` when `status == done`.
    On a `done` result these fields are populated (`unpacked_items` may be empty)."""
    input_summary: Optional[InputSummary] = Field(None, description="Roll-up counts for "
                                                  "the whole packing.")
    pallets: Optional[List[PalletOut]] = Field(None, description="Used pallets, each "
                                               "with its placed `items`.")
    unpacked_items: Optional[List[UnpackedItem]] = Field(None, description="Boxes that "
                                                         "did not fit (empty if all placed).")
    model_config = _LENIENT


class JobState(BaseModel):
    """Returned by `GET /jobs/{job_id}`. `status` drives which optional fields are
    present (optional null fields are omitted from the response, not sent as null)."""
    job_id: str = Field(..., description="The job id being polled.",
                        examples=["pk_3f8a1c9e4b7d4a2e9c1f"])
    status: Literal["queued", "running", "done", "failed", "timeout"] = Field(
        ..., description="`queued` → `running` → terminal. Values: `queued` (waiting "
        "for a worker), `running` (solving; may be skipped for fast jobs), `done` "
        "(succeeded — see `result`), `failed` (internal/solver error — see `error`), "
        "`timeout` (hit the hard wall-clock kill — see `error`). The terminal value "
        "comes from the finished task, not the queue. Keep polling until terminal.")
    result: Optional[PackResult] = Field(
        None, description="The packing plan — present **iff** `status == done`.")
    error: Optional[ErrorBody] = Field(
        None, title="Error", description="Failure detail — present **iff** `status` is "
        "`failed`/`timeout`.")
    meta: Optional[Dict[str, Any]] = Field(
        None, description="Reserved for timing/metadata; not populated in v1 (omitted).")

    model_config = {"json_schema_extra": {"examples": [
        {"job_id": "pk_3f8a1c9e4b7d4a2e9c1f", "status": "queued"},
        {"job_id": "pk_3f8a1c9e4b7d4a2e9c1f", "status": "running"},
        {"job_id": "pk_3f8a1c9e4b7d4a2e9c1f", "status": "done", "result": {
            "input_summary": {"items_packed": 2, "items_unpacked": 0,
                              "pallets_used": 1, "total_volume_utilisation": 0.13},
            "pallets": [{"pallet_id": "P001",
                         "dimensions": {"L": 1200, "W": 1000, "H": 1500},
                         "utilisation": 0.13, "cog": {"x": 600.0, "y": 500.0, "z": 75.0},
                         "total_weight": 7.5,
                         "items": [{"item_id": "B0001",
                                    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                                    "dimensions": {"L": 300, "W": 200, "H": 150},
                                    "orientation": {"perm": [0, 1, 2], "name": "LWH"},
                                    "weight": 2.5, "support_ratio": 1.0,
                                    "supported_by": ["floor"], "supports": []}]}],
            "unpacked_items": []}},
        {"job_id": "pk_3f8a1c9e4b7d4a2e9c1f", "status": "timeout",
         "error": {"code": "solve_timeout",
                   "message": "Exceeded the 120s wall-clock limit."}},
    ]}}
