"""Application settings.

Every value is read from the environment and validated once at import time. There are no
`os.getenv` calls anywhere else in the codebase — if something needs configuration, it is
declared here so that a missing or malformed value fails at startup rather than at 3am.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import (
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
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
    jwt_secret: SecretStr = SecretStr("dev-only-change-me")
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "bella-api"
    jwt_audience: str = "bella-app"
    access_token_ttl_seconds: int = 900  # 15 minutes
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 30  # 30 days

    # "mock" accepts unsigned developer tokens so the stack runs without a Firebase
    # project. The validator below refuses to let it reach a deployed environment.
    auth_provider: Literal["firebase", "mock"] = "mock"
    firebase_project_id: str | None = None
    firebase_certs_url: str = (
        "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
    )
    # Google rotates these daily and advertises the lifetime in Cache-Control; this is the
    # ceiling applied on top of it, not the refresh interval.
    firebase_certs_max_age_seconds: int = 3600

    # The refresh token is a cookie so that JavaScript cannot read it. Path-scoped to the
    # auth routes, which is the only place it is ever presented.
    refresh_cookie_name: str = "bella_refresh"
    refresh_cookie_path: str = "/v1/auth"
    refresh_cookie_domain: str | None = None
    refresh_cookie_secure: bool = True
    max_sessions_per_user: int = 10

    # ── rate limiting ────────────────────────────────────────────────────────
    rate_limit_enabled: bool = True
    rate_limit_default_per_minute: int = 300

    # ── observability ────────────────────────────────────────────────────────
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    sentry_dsn: SecretStr | None = None

    # ── providers ────────────────────────────────────────────────────────────
    # "mock" answers in-process: development and CI need no vendor account and no request
    # is ever billed. Gemini 2.5 Flash is the incumbent, carried over from the legacy
    # Cloud Run backend so the persona does not change in the same week as the platform.
    llm_provider: Literal["mock", "gemini"] = "mock"
    llm_model: str = "gemini-2.5-flash"
    llm_api_key: SecretStr | None = None
    llm_timeout_seconds: float = 60.0
    llm_max_output_tokens: int = 2048
    llm_temperature: float = 0.7

    # A second vendor behind the same interface. Unset means no fallback — the gateway is
    # what matters; which vendor sits behind it is a configuration decision.
    llm_fallback_provider: Literal["mock", "gemini"] | None = None
    llm_fallback_model: str | None = None

    gemini_api_base: str = "https://generativelanguage.googleapis.com/v1beta"

    tts_provider: str = "mock"
    tts_api_key: SecretStr | None = None
    mock_provider_url: str = "http://mock-providers:9000"

    # ── streaming ────────────────────────────────────────────────────────────
    # Deltas are mirrored into a Redis stream so a dropped connection resumes from
    # Last-Event-ID instead of regenerating — the reply is paid for once.
    sse_stream_ttl_seconds: int = 900
    sse_heartbeat_seconds: float = 15.0
    # Ceiling on one connection, so a client that never disconnects cannot hold a worker
    # slot forever.
    sse_max_duration_seconds: float = 300.0
    # How long one generation may hold its lock. Longer than a turn should ever take, short
    # enough that a crashed process does not wedge the message permanently.
    generation_lock_seconds: int = 300

    # ── context budget ───────────────────────────────────────────────────────
    # The prompt is assembled to fit a token budget, not a turn count. The legacy client
    # sent the entire untruncated history on every request while persisting only the last
    # 20 messages, so the prompt grew without bound until a reload silently discarded most
    # of the conversation.
    llm_context_budget_tokens: int = 8000
    # A hard ceiling on turns regardless of budget, so one pathological thread cannot
    # assemble a prompt out of thousands of tiny messages.
    llm_history_turns: int = 40

    # Summarisation runs when the verbatim tail crosses the trigger, and compresses until
    # it is back under the target. Staying ahead of the budget means the turn that crosses
    # the line is not the turn that pays for the summary.
    llm_summary_trigger_ratio: float = 0.7
    llm_summary_target_ratio: float = 0.45
    llm_summary_max_output_tokens: int = 500
    # Six words plus slack. A title is one line in a sidebar.
    llm_title_max_output_tokens: int = 32

    # Generations left on `streaming` by a process that died. Swept at startup, and only
    # once they are older than a generation could legitimately take — otherwise one replica
    # starting would fail another replica's in-flight reply.
    orphaned_generation_grace_seconds: int = 600

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

    @model_validator(mode="after")
    def _reject_development_auth_in_deployed_environments(self) -> Settings:
        """Fail at startup rather than authenticate anyone who asks.

        `local` and `test` are the only environments allowed to run without a real identity
        provider or with the placeholder signing key. Anywhere else, either mistake would
        mean forged sessions, so the process refuses to boot.
        """
        if self.environment in ("local", "test"):
            return self

        problems: list[str] = []
        if self.auth_provider == "mock":
            problems.append("AUTH_PROVIDER=mock")
        if self.jwt_secret.get_secret_value() == "dev-only-change-me":
            problems.append("JWT_SECRET is the development placeholder")
        if self.auth_provider == "firebase" and not self.firebase_project_id:
            problems.append("AUTH_PROVIDER=firebase requires FIREBASE_PROJECT_ID")

        if problems:
            raise ValueError(
                f"Unsafe auth configuration for environment={self.environment}: "
                + "; ".join(problems)
            )
        return self

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
