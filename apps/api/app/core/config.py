"""Application settings.

Every value is read from the environment and validated once at import time. There are no
`os.getenv` calls anywhere else in the codebase — if something needs configuration, it is
declared here so that a missing or malformed value fails at startup rather than at 3am.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "test", "preview", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    # ── application ──────────────────────────────────────────────────────────
    environment: Environment = "local"
    debug: bool = False
    service_name: str = "bella-api"
    version: str = "0.1.0"
    api_v1_prefix: str = "/v1"

    # ── networking ───────────────────────────────────────────────────────────
    host: str = "0.0.0.0"  # noqa: S104 — bound inside a container, not on the host
    port: int = 8000
    # NoDecode suppresses pydantic-settings' automatic JSON decoding of complex types,
    # which otherwise runs before the validator below and rejects a plain CSV value.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    trusted_hosts: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["*"])

    # ── datastores ───────────────────────────────────────────────────────────
    database_url: PostgresDsn
    database_pool_size: int = 10
    database_max_overflow: int = 5
    database_pool_timeout: int = 30
    database_echo: bool = False

    redis_url: RedisDsn

    # ── auth ─────────────────────────────────────────────────────────────────
    # Phase 2 populates these. Declared now so the contract is visible.
    jwt_secret: SecretStr = SecretStr("dev-only-change-me")
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 900  # 15 minutes
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 30  # 30 days
    firebase_project_id: str | None = None

    # ── rate limiting ────────────────────────────────────────────────────────
    rate_limit_enabled: bool = True
    rate_limit_default_per_minute: int = 300

    # ── observability ────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    sentry_dsn: SecretStr | None = None

    # ── providers (Phase 3+) ─────────────────────────────────────────────────
    llm_provider: str = "mock"
    llm_api_key: SecretStr | None = None
    tts_provider: str = "mock"
    tts_api_key: SecretStr | None = None
    mock_provider_url: str = "http://mock-providers:9000"

    # ── storage ──────────────────────────────────────────────────────────────
    storage_backend: Literal["local", "gcs"] = "local"
    storage_bucket_uploads: str = "bella-uploads"
    storage_local_path: str = "/var/lib/bella/storage"

    # ── mail ─────────────────────────────────────────────────────────────────
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_from: str = "bella@localhost"

    @field_validator("cors_origins", "trusted_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept a comma-separated string so compose files stay readable.

        A JSON array is still accepted, which keeps parity with how other list settings
        are supplied in Kubernetes-style manifests.
        """
        if isinstance(value, str):
            trimmed = value.strip()
            if trimmed.startswith("["):
                import json

                return json.loads(trimmed)
            return [item.strip() for item in trimmed.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def sqlalchemy_url(self) -> str:
        """asyncpg DSN. Pydantic renders `postgresql://`; SQLAlchemy needs the driver."""
        url = str(self.database_url)
        if url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def alembic_url(self) -> str:
        """Alembic runs migrations synchronously via psycopg-style DSN over asyncpg."""
        return self.sqlalchemy_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Every field is either optional or supplied by the environment; pydantic-settings
    # populates them, which mypy cannot infer from the constructor signature.
    return Settings()


SettingsDep = Annotated[Settings, Field()]
