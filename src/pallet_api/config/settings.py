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
    # Round 5 (R6): a BARE `PALLET_API_X=` key (empty value — a common
    # compose/.env artifact) means "use the default", NOT False. Only an
    # explicit falsy string turns a flag off; previously an empty value
    # silently disabled default-ON features like the realism passes.
    v = os.getenv(name)
    if v is None or not v.strip():
        return default
    return v.strip().lower() not in ("0", "false", "no", "off")


def _list(name: str, default: str) -> list[str]:
    """Parse a comma-separated env var into a stripped, non-empty list.
    Unlike _b, an explicitly EMPTY value means "empty list", not "default" —
    cors_origins uses that as the deliberate opt-out (no origins = CORS off)."""
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


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
    # Separate, generous cap for result polling (GET /jobs/*) — round 5
    # (R4): polls used to be completely uncapped per IP.
    poll_rate_limit_per_min: int = _i("PALLET_API_POLL_RATE_LIMIT_PER_MIN",
                                      600)

    # --- CORS (browser cross-origin access) ---
    # The static front-end calls this API DIRECTLY from the browser, so the API must
    # send Access-Control-Allow-Origin for the front-end's serving origin — otherwise the
    # browser blocks every request and the UI shows "Couldn't reach the packing service".
    # Comma-separated list of allowed origins; "*" (the default) allows ANY origin, which
    # is safe here because this is a no-login API that uses no cookies/credentials. Lock it
    # down to your front-end origin(s) in production if you prefer, e.g.
    #   PALLET_API_CORS_ORIGINS=https://app.example.com,https://www.example.com
    # Explicitly EMPTY (PALLET_API_CORS_ORIGINS=) = no origins = the CORS
    # middleware is not installed at all (the round-5 server-to-server posture).
    cors_origins: list[str] = _list("PALLET_API_CORS_ORIGINS", "*")

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


def _validate(s: "Settings") -> None:
    """Fail fast at import on configs that would produce a SILENT outage.

    Round 5 (R2): `PALLET_API_HARD_BUDGET_S=0` (or negative, or below the
    soft budget) used to boot cleanly and then turn EVERY job into
    `timeout` while /health stayed green; a zero rate limit 429'd every
    request. Both the API and the worker import this module, so a bad env
    now refuses to start instead of serving a dead deployment.
    """
    problems = []
    if not (0 < s.soft_budget_s < s.hard_budget_s):
        problems.append(
            f"budgets must satisfy 0 < SOFT ({s.soft_budget_s}) < HARD "
            f"({s.hard_budget_s}) — PALLET_API_SOFT_BUDGET_S / "
            f"PALLET_API_HARD_BUDGET_S")
    if s.rate_limit_per_min <= 0:
        problems.append(
            f"PALLET_API_RATE_LIMIT_PER_MIN must be > 0 "
            f"(got {s.rate_limit_per_min})")
    if s.poll_rate_limit_per_min <= 0:
        problems.append(
            f"PALLET_API_POLL_RATE_LIMIT_PER_MIN must be > 0 "
            f"(got {s.poll_rate_limit_per_min})")
    if s.max_body_bytes <= 0:
        problems.append(
            f"PALLET_API_MAX_BODY_BYTES must be > 0 (got {s.max_body_bytes})")
    if s.result_ttl_s <= 0:
        problems.append(
            f"PALLET_API_RESULT_TTL_S must be > 0 (got {s.result_ttl_s})")
    if s.max_boxes <= 0:
        problems.append(f"PALLET_API_MAX_BOXES must be > 0 (got {s.max_boxes})")
    if problems:
        raise RuntimeError(
            "invalid PALLET_API configuration:\n  - " + "\n  - ".join(problems))


settings = Settings()
_validate(settings)
