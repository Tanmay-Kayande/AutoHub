"""Typed application settings.

Only ``DATABASE_URL`` and ``SECRET_KEY`` are required to start the API. Keys for
external services are optional and are demanded by the component that uses them
(see :meth:`Settings.require_gemini_key`), so tests and read-only API use need
no paid credentials.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from autohub.core.errors import ConfigurationError

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "autohub"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PACKAGE_ROOT / ".env", REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"

    # --- required ---
    database_url: str
    # HS256 tokens signed with a short key can be brute-forced offline and then forged.
    secret_key: SecretStr = Field(min_length=32)

    # --- auth ---
    algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=60, ge=1)

    # --- database pool ---
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)

    # --- external services (optional) ---
    gemini_api_key: SecretStr | None = None
    serpapi_key: SecretStr | None = None

    # --- LLM / embeddings ---
    llm_provider: str = "gemini"
    llm_model: str = "gemini-3.5-flash-lite"  # assistant; own free-tier quota, separate from extraction
    extraction_model: str = "gemini-2.5-flash"
    llm_timeout_seconds: float = Field(default=180.0, gt=0)
    llm_max_attempts: int = Field(default=4, ge=1)
    embedding_provider: str = "gemini"
    embedding_model: str = "gemini-embedding-001"
    embedding_version: str = "v1"

    # --- ingestion ---
    data_dir: Path = REPO_ROOT / "data"
    enabled_brands: str = "mahindra,hyundai,tata"
    crawler_user_agent: str = "AutoHubBot/2.0 (+automotive brochure indexer; contact: site owner)"
    crawler_delay_seconds: float = Field(default=2.0, ge=0)
    crawler_max_pages_per_brand: int = Field(default=60, ge=1)
    http_timeout_seconds: float = Field(default=30.0, gt=0)
    pipeline_max_extractions_per_run: int = Field(default=15, ge=0)
    pipeline_max_image_searches_per_run: int = Field(default=20, ge=0)
    pipeline_cron: str = "0 3 1 * *"  # 03:00 UTC on the 1st of each month
    images_target_per_category: int = Field(default=5, ge=1)

    # --- API ---
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    scheduler_enabled: bool = True
    rate_limit_enabled: bool = True

    # ------------------------------------------------------------------ helpers
    @property
    def enabled_brand_keys(self) -> list[str]:
        return [b.strip().lower() for b in self.enabled_brands.split(",") if b.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def brochure_dir(self) -> Path:
        return self.data_dir / "brochures"

    def require_gemini_key(self) -> str:
        if self.gemini_api_key is None or not self.gemini_api_key.get_secret_value():
            raise ConfigurationError("GEMINI_API_KEY is not configured.")
        return self.gemini_api_key.get_secret_value()

    def require_serpapi_key(self) -> str:
        if self.serpapi_key is None or not self.serpapi_key.get_secret_value():
            raise ConfigurationError("SERPAPI_KEY is not configured.")
        return self.serpapi_key.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # values come from the environment
