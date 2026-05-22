"""Configuration loaded from environment / .env file via pydantic-settings.

The Settings object is the single source of truth for all runtime config.
Never read os.environ directly elsewhere in the codebase.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Loaded from .env at startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── Supabase ────────────────────────────────────────────────────────────
    supabase_url: str = Field(..., description="Supabase project URL")
    supabase_anon_key: SecretStr = Field(..., description="Public anon key")
    supabase_service_role_key: SecretStr = Field(
        ..., description="Service role key (bypasses RLS); scrapers only"
    )
    supabase_db_url: SecretStr | None = Field(
        default=None, description="Direct Postgres URL (optional, for bulk ops)"
    )

    # ─── Dhan API ────────────────────────────────────────────────────────────
    dhan_client_id: str | None = None
    dhan_access_token: SecretStr | None = None

    # ─── Per-source rate limits (requests per second) ───────────────────────
    dhan_rate_limit_rps: float = 2.0
    mfapi_rate_limit_rps: float = 5.0
    rupeevest_rate_limit_rps: float = 1.0
    trendlyne_rate_limit_rps: float = 0.3
    scan360_rate_limit_rps: float = 2.0

    # ─── Circuit breaker ────────────────────────────────────────────────────
    circuit_breaker_threshold: int = Field(
        default=5, description="Consecutive failures before circuit opens"
    )

    # ─── HTTP client tuning ─────────────────────────────────────────────────
    http_timeout_seconds: float = 30.0
    http_connect_timeout_seconds: float = 10.0
    scraper_user_agent: str = (
        "dastock/0.1.0 (open-source; github.com/MarketMascot/datamascot)"
    )

    # ─── Run metadata ───────────────────────────────────────────────────────
    triggered_by: Literal["cron", "manual", "retry", "bootstrap"] = "manual"

    # ─── Alerting (optional) ────────────────────────────────────────────────
    alert_webhook_url: SecretStr | None = None

    def rate_limit_for(self, source: str) -> float:
        """Return the configured RPS for a given source name."""
        return float(getattr(self, f"{source}_rate_limit_rps", 1.0))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton accessor. Tests can clear the cache via `get_settings.cache_clear()`."""
    return Settings()  # type: ignore[call-arg]
