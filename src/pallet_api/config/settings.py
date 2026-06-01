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


class Settings:
    # --- Redis / queue ---
    redis_url: str = os.getenv("PALLET_API_REDIS_URL", "redis://localhost:6379")

    # --- input limits (the boundary contract) ---
    max_boxes: int = _i("PALLET_API_MAX_BOXES", 500)

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

    # --- abuse protection ---
    rate_limit_per_min: int = _i("PALLET_API_RATE_LIMIT_PER_MIN", 30)

    # --- meta ---
    version: str = os.getenv("PALLET_API_VERSION", "0.1.0")
    # The core algorithm has no __version__; its version is the pinned git ref it
    # was installed from (prod) or "dev-mount" (dev). Surfaced by GET /version.
    core_version: str = os.getenv("PALLET_API_CORE_VERSION", "unknown")

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
        }


settings = Settings()
