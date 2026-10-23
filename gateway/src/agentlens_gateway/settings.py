"""Configuration from environment variables (twelve-factor). Validated once at startup."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql://agentlens:agentlens@localhost:5432/agentlens"
    agent_base_url: str = "http://localhost:8200"
    admin_api_key: str = ""
    allowed_origins: tuple[str, ...] = ("http://localhost:4200",)
    daily_cost_cap_usd: float = 5.0
    run_timeout_s: float = 180.0  # wall-clock cap for one run (terminal reason: timeout)
    max_concurrent_runs: int = 8  # excess runs wait in 'queued'
    default_rate_limit_per_min: int = 60  # applies to POST /runs, per API key
    auto_migrate: bool = True
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR
    pool_max_size: int = 10

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        d = cls()
        return cls(
            database_url=e.get("DATABASE_URL", d.database_url),
            agent_base_url=e.get("AGENT_BASE_URL", d.agent_base_url).rstrip("/"),
            admin_api_key=e.get("ADMIN_API_KEY", ""),
            allowed_origins=tuple(
                o.strip()
                for o in e.get("ALLOWED_ORIGINS", ",".join(d.allowed_origins)).split(",")
                if o.strip()
            ),
            daily_cost_cap_usd=float(e.get("DAILY_COST_CAP_USD", d.daily_cost_cap_usd)),
            run_timeout_s=float(e.get("RUN_TIMEOUT_S", d.run_timeout_s)),
            max_concurrent_runs=int(e.get("MAX_CONCURRENT_RUNS", d.max_concurrent_runs)),
            default_rate_limit_per_min=int(
                e.get("DEFAULT_RATE_LIMIT_PER_MIN", d.default_rate_limit_per_min)
            ),
            auto_migrate=e.get("AUTO_MIGRATE", "true").lower() != "false",
            migrations_dir=Path(e.get("MIGRATIONS_DIR", str(d.migrations_dir))),
            pool_max_size=int(e.get("DB_POOL_MAX", d.pool_max_size)),
        )

    def validate(self) -> None:
        """Refuse to start misconfigured: an open or default-credential control plane is worse than no control plane."""
        if len(self.admin_api_key) < 16:
            raise ValueError(
                "ADMIN_API_KEY must be set to at least 16 characters (generate one with: openssl rand -hex 32)"
            )
        if self.daily_cost_cap_usd <= 0:
            raise ValueError("DAILY_COST_CAP_USD must be > 0")
        if self.max_concurrent_runs < 1 or self.run_timeout_s <= 0:
            raise ValueError("MAX_CONCURRENT_RUNS must be >= 1 and RUN_TIMEOUT_S > 0")
        if "*" in self.allowed_origins:
            raise ValueError("ALLOWED_ORIGINS must list explicit origins, not '*'")
