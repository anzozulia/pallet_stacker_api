"""Environment-driven configuration (12-factor). All knobs are env vars with the
prefix PALLET_API_. See docs/03_design_decisions.md (D9).

No external settings library — plain os.getenv keeps the dependency surface and
the import cost (this is imported in the lightweight API process too) minimal.
"""
from __future__ import annotations

import os


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _b(name: str, default: bool) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() not in (
        "0", "false", "no", "off", "")


class Settings:
    # --- Redis / queue ---
    redis_url: str = os.getenv("PALLET_API_REDIS_URL", "redis://localhost:6379")

    # --- input limits (the boundary contract) ---
    max_boxes: int = _i("PALLET_API_MAX_BOXES", 500)
    # Request-body byte cap (413 over it). A legitimate 500-box request is a
    # few hundred KB; the cap bounds the RAM amplification of hostile bodies
    # across uvicorn/pydantic/Redis/pickle (hardening plan C2/F6).
    max_body_bytes: int = _i("PALLET_API_MAX_BODY_BYTES", 10 * 1024 * 1024)

    # --- time budgets (seconds) ---
    # soft = the solver's own time_limit_s ceiling (clamps caller requests).
    # hard = the worker's wall-clock kill, set above soft so a clean finish wins.
    soft_budget_s: float = _f("PALLET_API_SOFT_BUDGET_S", 90.0)
    hard_budget_s: float = _f("PALLET_API_HARD_BUDGET_S", 120.0)

    # --- result lifetime (ephemeral; no history) ---
    result_ttl_s: int = _i("PALLET_API_RESULT_TTL_S", 3600)

    # --- solver defaults ---
    default_max_pallets: int = _i("PALLET_API_DEFAULT_MAX_PALLETS", 1)
    default_seed: int = _i("PALLET_API_DEFAULT_SEED", 42)
    population_size: int = _i("PALLET_API_POPULATION_SIZE", 300)
    n_populations: int = _i("PALLET_API_N_POPULATIONS", 3)
    patience: int = _i("PALLET_API_PATIENCE", 150)
    n_modes: int = _i("PALLET_API_N_MODES", 6)

    # --- realism layer (D14) ---
    # Default ON for the service (the core's own defaults are OFF). Each is
    # an independent kill-switch for rollback/debugging.
    recenter: bool = _b("PALLET_API_RECENTER", True)
    align_orientations: bool = _b("PALLET_API_ALIGN_ORIENTATIONS", True)
    realism_weight: float = _f("PALLET_API_REALISM_WEIGHT", 1.0)
    # Transitive load bearing (hardening round 2, F19): each box's weight
    # propagates down the whole support chain against max_load_on_top —
    # physically correct; stacked fragile loads pack fewer boxes than the
    # historical direct-only model. Kill-switch for the old behavior.
    transitive_load: bool = _b("PALLET_API_TRANSITIVE_LOAD", True)

    # --- abuse protection ---
    rate_limit_per_min: int = _i("PALLET_API_RATE_LIMIT_PER_MIN", 30)

    # --- meta ---
    version: str = os.getenv("PALLET_API_VERSION", "0.1.0")
    # The core algorithm has no __version__; this reports the vendored core version
    # (see core/VENDOR.md). Surfaced by GET /version. Override per deployment.
    core_version: str = os.getenv("PALLET_API_CORE_VERSION", "vendored")

    def solver_cfg(self) -> dict:
        """The picklable settings bundle passed into the solve subprocess."""
        return {
            "max_boxes": self.max_boxes,
            "soft_budget_s": self.soft_budget_s,
            "default_max_pallets": self.default_max_pallets,
            "default_seed": self.default_seed,
            "population_size": self.population_size,
            "n_populations": self.n_populations,
            "patience": self.patience,
            "n_modes": self.n_modes,
            "recenter": self.recenter,
            "align_orientations": self.align_orientations,
            "realism_weight": self.realism_weight,
            "transitive_load": self.transitive_load,
        }


settings = Settings()
