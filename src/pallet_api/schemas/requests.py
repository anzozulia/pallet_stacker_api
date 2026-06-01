"""Request schemas (pydantic v2). These enforce *shape, type, and numeric bounds*
(→ `422 validation_error`); the integer-ness, ≤500-box cap, finite-cap and
uniqueness rules of the **contract** are enforced by the core input gate
(→ `400 invalid_input`). See the boundary note on `POST /pack`.

Every field carries a `description` + `examples`, so the Swagger UI (`/docs`) and
the OpenAPI schema fully document the request without any external reference.
See docs/02_api_contract.md.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class BoxIn(BaseModel):
    """One box to pack. Spatial dimensions are integers in any unit (mm/cm/inch),
    applied consistently across the whole request."""
    id: str = Field(..., description="Unique box id; echoed in the result so you can "
                    "map each placement back to its input. Duplicate ids → `400`.",
                    examples=["B0001"])
    length: int = Field(..., gt=0, description="Length — a positive INTEGER (any unit, "
                        "consistent across the request). Non-integers are rejected "
                        "(`422`), never rounded.", examples=[300])
    width: int = Field(..., gt=0, description="Width — a positive integer.", examples=[200])
    height: int = Field(..., gt=0, description="Height — a positive integer.", examples=[150])
    weight: float = Field(0.0, ge=0, description="Box weight (any consistent unit; may "
                          "be fractional). Default 0.", examples=[2.5])
    max_load_on_top: Optional[float] = Field(
        None, ge=0, description="Max weight this box can bear stacked on top. Omitted "
        "or `null` = unlimited (default, equivalent); `0` = fragile (nothing may be "
        "placed on it).", examples=[20.0])
    rotations: Literal["all", "this_side_up", "none"] = Field(
        "all", description="Allowed orientations: `all` = any of the 6; "
        "`this_side_up` = no tipping (height stays vertical); `none` = fixed as given.")
    group: Optional[str] = Field(
        None, description="Optional co-location key — boxes sharing a group are kept on "
        "the same pallet. Only takes effect when `options.max_pallets > 1`; ignored "
        "(no error) when packing into a single pallet.", examples=["CUST1"])
    requires_full_support: bool = Field(
        False, description="If true, this box must be 100% supported from below, "
        "overriding the global `options.support_ratio` for this box (even when "
        "`support_ratio` is lower or 0). Default false → the global `support_ratio` "
        "applies.")


class PalletIn(BaseModel):
    """The target pallet / container. Dimensions are integers in the same unit as
    the boxes."""
    length: int = Field(..., gt=0, description="Pallet length — a positive integer "
                        "(same unit as the boxes).", examples=[1200])
    width: int = Field(..., gt=0, description="Pallet width — a positive integer.",
                       examples=[1000])
    height: int = Field(..., gt=0, description="Pallet/stack height limit — a positive "
                        "integer.", examples=[1500])
    max_weight: Optional[float] = Field(
        None, ge=0, description="Max total weight the pallet can carry. Omitted or "
        "`null` = unlimited (default, equivalent).", examples=[900])
    max_overhang: int = Field(
        0, ge=0, description="Allowed horizontal overhang beyond the pallet footprint "
        "(same unit, integer). `0` (default) = boxes must stay within the footprint.",
        examples=[0])


class OptionsIn(BaseModel):
    """Solver options — all optional, with service defaults."""
    max_pallets: int = Field(
        1, ge=1, description="Max number of pallets/containers to use. `1` (default) = "
        "pack into a single container; `>1` enables multi-pallet packing and `group` "
        "co-location.", examples=[1])
    time_budget_s: Optional[float] = Field(
        None, gt=0, description="Requested solver time budget (seconds). Silently "
        "**clamped** to `[1 s, the service ceiling]` — never rejected for being out of "
        "range. The ceiling is the configurable soft budget (`PALLET_API_SOFT_BUDGET_S`, "
        "default 90 s; the hard wall-clock kill sits ~30 s above it). `null` (default) "
        "= the service default. Solves usually converge well before the budget — small "
        "inputs finish in well under a second.", examples=[30])
    support_ratio: float = Field(
        0.8, ge=0, le=1, description="Global minimum fraction [0,1] of each box's base "
        "that must be supported from below. `0.8` (default) enforces physical "
        "stability; `0` disables the global minimum. A box with "
        "`requires_full_support=true` is always held to 100% regardless of this value.",
        examples=[0.8])
    seed: Optional[int] = Field(
        None, description="RNG seed for reproducibility — same input + same seed ⇒ "
        "identical plan. `null` (default) = the service default seed.", examples=[42])


class PackRequest(BaseModel):
    """A packing job: the boxes, the target pallet, and the solver options."""
    boxes: List[BoxIn] = Field(..., min_length=1,
                               description="The boxes to pack. At least 1; at most the "
                               "service box cap (default 500) — over-cap requests are "
                               "rejected with `400`.")
    pallet: PalletIn = Field(..., description="The target pallet/container.")
    options: OptionsIn = Field(default_factory=OptionsIn,
                               description="Solver options (optional).")

    model_config = {"json_schema_extra": {"examples": [{
        "boxes": [
            {"id": "B0001", "length": 300, "width": 200, "height": 150, "weight": 2.5,
             "max_load_on_top": 20.0, "rotations": "this_side_up"},
            {"id": "B0002", "length": 400, "width": 300, "height": 300, "weight": 5.0},
        ],
        "pallet": {"length": 1200, "width": 1000, "height": 1500, "max_weight": 900},
        "options": {"max_pallets": 1, "time_budget_s": 30, "seed": 42},
    }]}}
