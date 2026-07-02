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

from pydantic import BaseModel, Field, model_validator

# Weight-like caps are bounded well under the core's 1e18 "no limit" sentinel
# (see the core gate) — and 1e12 of any real unit is already absurd for a pallet.
_MAX_WEIGHT = 1e12
# Spatial dims are bounded so every int64 product in the core's numpy/numba hot
# paths provably fits: with dims <= 1e6, areas <= 1e12 and volumes <= 1e18 <
# int64 max (9.22e18). 1e6 units ≈ 1 km in mm — pick a smaller unit if you
# exceed it. (Hardening round 2, F16.)
_MAX_DIM = 1_000_000


class BoxIn(BaseModel):
    """One box to pack. Spatial dimensions are integers in any unit (mm/cm/inch),
    applied consistently across the whole request."""
    id: str = Field(..., min_length=1, max_length=128,
                    description="Unique box id (1–128 chars); echoed in the result "
                    "so you can map each placement back to its input. Duplicate "
                    "ids → `400`.",
                    examples=["B0001"])
    length: int = Field(..., gt=0, le=_MAX_DIM,
                        description="Length — a positive INTEGER ≤ 1e6 (any unit, "
                        "consistent across the request; 1e6 ≈ 1 km in mm — pick a "
                        "smaller unit if you exceed it). Non-integers are rejected "
                        "(`422`), never rounded.", examples=[300])
    width: int = Field(..., gt=0, le=_MAX_DIM,
                       description="Width — a positive integer ≤ 1e6.", examples=[200])
    height: int = Field(..., gt=0, le=_MAX_DIM,
                        description="Height — a positive integer ≤ 1e6.", examples=[150])
    weight: float = Field(0.0, ge=0, le=_MAX_WEIGHT, allow_inf_nan=False,
                          description="Box weight (any consistent unit; may "
                          "be fractional, at most 1e12). Default 0.", examples=[2.5])
    max_load_on_top: Optional[float] = Field(
        None, ge=0, le=_MAX_WEIGHT, allow_inf_nan=False,
        description="Max weight this box can bear stacked on top (at most 1e12). "
        "Omitted or `null` = unlimited (default, equivalent); `0` = fragile (nothing "
        "may be placed on it). NOTE: this caps stacked WEIGHT only — it does not "
        "stop the box being tipped on its side to fit; orientation-sensitive goods "
        "also need `rotations: \"this_side_up\"`.", examples=[20.0])
    rotations: Literal["all", "this_side_up", "none"] = Field(
        "all", description="Allowed orientations: `all` = any of the 6; "
        "`this_side_up` = no tipping (height stays vertical); `none` = fixed as given.")
    group: Optional[str] = Field(
        None, max_length=128, description="Optional co-location key (≤128 chars) — "
        "boxes sharing a group are kept on the same pallet. Only takes effect when "
        "`options.max_pallets > 1`; ignored (no error) when packing into a single "
        "pallet. Empty / whitespace-only = no group.", examples=["CUST1"])
    requires_full_support: bool = Field(
        False, description="If true, this box must be 100% supported from below, "
        "overriding the global `options.support_ratio` for this box (even when "
        "`support_ratio` is lower or 0). Default false → the global `support_ratio` "
        "applies.")


class PalletIn(BaseModel):
    """The target pallet / container. Dimensions are integers in the same unit as
    the boxes."""
    length: int = Field(..., gt=0, le=_MAX_DIM,
                        description="Pallet length — a positive integer ≤ 1e6 "
                        "(same unit as the boxes).", examples=[1200])
    width: int = Field(..., gt=0, le=_MAX_DIM,
                       description="Pallet width — a positive integer ≤ 1e6.",
                       examples=[1000])
    height: int = Field(..., gt=0, le=_MAX_DIM,
                        description="Pallet/stack height limit — a positive "
                        "integer ≤ 1e6.", examples=[1500])
    max_weight: Optional[float] = Field(
        None, ge=0, le=_MAX_WEIGHT, allow_inf_nan=False,
        description="Max total weight the pallet can carry (at most 1e12). Omitted or "
        "`null` = unlimited (default, equivalent).", examples=[900])
    max_overhang: int = Field(
        0, ge=0, description="Allowed horizontal overhang beyond the pallet footprint "
        "(same unit, integer; at most `min(length, width)`). `0` (default) = boxes "
        "must stay within the footprint.",
        examples=[0])

    @model_validator(mode="after")
    def _overhang_within_footprint(self):
        # An overhang larger than the deck itself lets a box sit fully off the
        # pallet while staying contract-"valid" (hardening plan C1/F11).
        limit = min(self.length, self.width)
        if self.max_overhang > limit:
            raise ValueError(
                f"max_overhang ({self.max_overhang}) must not exceed the smaller "
                f"footprint dimension (min(length, width) = {limit})")
        return self


class OptionsIn(BaseModel):
    """Solver options — all optional, with service defaults."""
    max_pallets: int = Field(
        1, ge=1, le=100, description="Max number of pallets/containers to use (1–100). "
        "`1` (default) = pack into a single container; `>1` enables multi-pallet "
        "packing and `group` co-location.", examples=[1])
    time_budget_s: Optional[float] = Field(
        None, gt=0, allow_inf_nan=False,
        description="Requested solver time budget (seconds). Silently "
        "**clamped** to `[1 s, the service ceiling]` — never rejected for being out of "
        "range. The ceiling is the configurable soft budget (`PALLET_API_SOFT_BUDGET_S`, "
        "default 90 s; the hard wall-clock kill sits ~30 s above it). `null` (default) "
        "= the service default. Solves usually converge well before the budget — small "
        "inputs finish in well under a second.", examples=[30])
    support_ratio: float = Field(
        0.8, ge=0, le=1, allow_inf_nan=False,
        description="Global minimum fraction [0,1] of each box's base "
        "that must be supported from below. `0.8` (default) enforces physical "
        "stability; `0` disables the global minimum. A box with "
        "`requires_full_support=true` is always held to 100% regardless of this value.",
        examples=[0.8])
    seed: Optional[int] = Field(
        None, ge=0, description="Non-negative RNG seed for reproducibility — same "
        "input + same seed ⇒ identical plan. `null` (default) = the service default "
        "seed. On highly symmetric loads (e.g. all-identical boxes) DIFFERENT seeds "
        "can legitimately return identical plans — every dense flat layout ties.",
        examples=[42])


class PackRequest(BaseModel):
    """A packing job: the boxes, the target pallet, and the solver options."""
    boxes: List[BoxIn] = Field(..., min_length=1,
                               description="The boxes to pack. At least 1; at most the "
                               "service box cap (default 500) — over-cap requests are "
                               "rejected with `400`.")
    pallet: PalletIn = Field(..., description="The target pallet/container.")
    options: Optional[OptionsIn] = Field(default_factory=OptionsIn,
                                         description="Solver options (optional; "
                                         "omitting and `null` are equivalent).")

    @model_validator(mode="after")
    def _null_options_means_defaults(self):
        # `"options": null` and omission are equivalent (same convention as
        # the unlimited caps).
        if self.options is None:
            self.options = OptionsIn()
        return self

    model_config = {"json_schema_extra": {"examples": [{
        "boxes": [
            {"id": "B0001", "length": 300, "width": 200, "height": 150, "weight": 2.5,
             "max_load_on_top": 20.0, "rotations": "this_side_up"},
            {"id": "B0002", "length": 400, "width": 300, "height": 300, "weight": 5.0},
        ],
        "pallet": {"length": 1200, "width": 1000, "height": 1500, "max_weight": 900},
        "options": {"max_pallets": 1, "time_budget_s": 30, "seed": 42},
    }]}}
