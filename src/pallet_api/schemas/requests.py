"""Request schemas (pydantic v2). Shape/type validation only — the *contract*
(positive-integer dims, ≤max_boxes, finite caps, unique ids) is enforced by the
core input gate so all contract errors share one message format. See
docs/02_api_contract.md.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class BoxIn(BaseModel):
    id: str
    length: float            # gate enforces positive integer
    width: float
    height: float
    weight: float = 0.0
    max_load_on_top: Optional[float] = None      # None = unlimited; 0 = fragile
    rotations: Literal["all", "this_side_up", "none"] = "all"
    group: Optional[str] = None
    requires_full_support: bool = False


class PalletIn(BaseModel):
    length: float
    width: float
    height: float
    max_weight: Optional[float] = None           # None = unlimited
    max_overhang: float = 0.0


class OptionsIn(BaseModel):
    max_pallets: int = 1
    time_budget_s: Optional[float] = None         # clamped to the service ceiling
    support_ratio: float = 0.8
    seed: Optional[int] = None


class PackRequest(BaseModel):
    boxes: List[BoxIn] = Field(..., min_length=1)
    pallet: PalletIn
    options: OptionsIn = Field(default_factory=OptionsIn)
