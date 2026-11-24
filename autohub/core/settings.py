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

from pydantic import Field, SecretStr, field_validator
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
    # Schema owner used only by Alembic. The app connects with DATABASE_URL, which should be
    # a role without DDL rights (see tools/provision_app_role.py). Unset: Alembic uses DATABASE_URL.
    migration_database_url: str | None = None
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
    # True: GEMINI_API_KEY is a Vertex AI key and calls are billed to its Google Cloud project.
    gemini_use_vertexai: bool = False
    serpapi_key: SecretStr | None = None

    # --- LLM / embeddings ---
    llm_provider: str = "gemini"
    llm_model: str = "gemini-3.5-flash-lite"  # assistant; own free-tier quota, separate from extraction
    extraction_model: str = "gemini-3.5-flash"  # prompt v2.3 was tuned and checked on this model
    llm_timeout_seconds: float = Field(default=180.0, gt=0)
    llm_max_attempts: int = Field(default=4, ge=1)
    # Extraction output runs to tens of thousands of tokens (about 230 tokens/s measured), so
    # a brochure with many variants needs minutes, not the assistant's timeout. Every attempt
    # is a request against the daily quota, so a timed-out extraction is retried once only.
    extraction_timeout_seconds: float = Field(default=600.0, gt=0)
    extraction_max_attempts: int = Field(default=2, ge=1)
    # A plain rate limit (HTTP 429, not a daily quota or the spend cap) is refused before any
    # work, so extraction waits it out on its own, longer budget: up to this many rate-limited
    # requests, sleeping base * 2^n s (capped) between them - 15+30+60+120+240 s by default.
    # Flex PayGo is throttled more often. A rate limit that outlasts this still stops the run.
    extraction_rate_limit_attempts: int = Field(default=6, ge=1)
    extraction_rate_limit_base_delay_seconds: float = Field(default=15.0, gt=0)
    extraction_rate_limit_max_delay_seconds: float = Field(default=300.0, gt=0)
    # Gemini 3.x thinking level for extraction (thinking tokens are billed as output); unset: model
    # default. Measured on Venue with 3.5-flash: "low" listed 2 of 23 variants, "medium" saved nothing.
    extraction_thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    # Vertex AI only: extract on the Flex PayGo tier (about half price, slower).
    extraction_flex: bool = False
    embedding_provider: str = "gemini"
    embedding_model: str = "gemini-embedding-001"
    embedding_version: str = "v1"

    # --- ingestion ---
    data_dir: Path = REPO_ROOT / "data"
    enabled_brands: str = "mahindra,hyundai,tata,kia,maruti,toyota,honda,mg"
    crawler_user_agent: str = "AutoHubBot/2.0 (+automotive brochure indexer; contact: site owner)"
    crawler_delay_seconds: float = Field(default=2.0, ge=0)
    crawler_max_pages_per_brand: int = Field(default=60, ge=1)
    http_timeout_seconds: float = Field(default=30.0, gt=0)
    pipeline_max_extractions_per_run: int = Field(default=15, ge=0)
    pipeline_max_image_searches_per_run: int = Field(default=20, ge=0)
    pipeline_cron: str = "0 3 1 * *"  # 03:00 UTC on the 1st of each month
    images_target_per_category: int = Field(default=5, ge=1)
    # A slot whose last search added nothing is not searched again for this many days.
    images_retry_after_days: int = Field(default=30, ge=0)

    # Assistant questions per client per rolling 24 h without signing in (0: sign-in required).
    assistant_anonymous_questions: int = Field(default=2, ge=0)

    # --- API ---
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    scheduler_enabled: bool = True
    rate_limit_enabled: bool = True

    @field_validator("extraction_thinking_level", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        return None if isinstance(value, str) and not value.strip() else value

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
