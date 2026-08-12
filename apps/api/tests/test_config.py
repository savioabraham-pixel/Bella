"""Settings parsing.

These are pure unit tests — no database, no Redis — covering the conversions that bit
during bring-up and would otherwise fail only at container start.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.config import Settings


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Strip inherited configuration.

    These tests assert what Settings does with a given input, so the container's own
    DATABASE_URL and friends must not leak in — otherwise the "missing required setting"
    case would pass only because the environment happened to supply it.
    """
    for key in list(os.environ):
        if key.isupper():
            monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)  # somewhere with no .env to discover


BASE_ENV = {
    "DATABASE_URL": "postgresql://u:p@db:5432/bella",
    "REDIS_URL": "redis://cache:6379/0",
}


def _settings(**overrides: str) -> Settings:
    return Settings(**{**BASE_ENV, **overrides})  # type: ignore[arg-type]


def test_cors_origins_accepts_csv() -> None:
    """Compose files supply a plain comma-separated list, not JSON."""
    settings = _settings(CORS_ORIGINS="http://a.test, http://b.test")

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_accepts_json_array() -> None:
    settings = _settings(CORS_ORIGINS='["http://a.test","http://b.test"]')

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_cors_origins_ignores_empty_entries() -> None:
    settings = _settings(CORS_ORIGINS="http://a.test,,  ,http://b.test")

    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_sqlalchemy_url_gets_the_asyncpg_driver() -> None:
    """Pydantic renders `postgresql://`; SQLAlchemy needs the driver named."""
    settings = _settings()

    assert settings.sqlalchemy_url.startswith("postgresql+asyncpg://")


# A deployed environment has to satisfy the auth validator, so these are the minimum
# credentials any non-local Settings must carry.
DEPLOYED_AUTH = {
    "AUTH_PROVIDER": "firebase",
    "FIREBASE_PROJECT_ID": "bella-prod",
    "JWT_SECRET": "a-real-secret",
}


def test_is_production_flag() -> None:
    assert _settings(ENVIRONMENT="production", **DEPLOYED_AUTH).is_production is True
    assert _settings(ENVIRONMENT="local").is_production is False


def test_mock_auth_is_refused_outside_local_and_test() -> None:
    """The development identity provider must not be reachable from a deployed environment.

    `mock` believes whatever an unsigned token claims, so shipping it would mean anyone
    could mint a session as anyone.
    """
    with pytest.raises(ValueError, match="AUTH_PROVIDER=mock"):
        _settings(ENVIRONMENT="staging", JWT_SECRET="a-real-secret")


def test_placeholder_jwt_secret_is_refused_outside_local_and_test() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET is the development placeholder"):
        _settings(
            ENVIRONMENT="production",
            AUTH_PROVIDER="firebase",
            FIREBASE_PROJECT_ID="bella-prod",
        )


def test_firebase_provider_requires_a_project_id() -> None:
    with pytest.raises(ValueError, match="FIREBASE_PROJECT_ID"):
        _settings(
            ENVIRONMENT="production",
            AUTH_PROVIDER="firebase",
            JWT_SECRET="a-real-secret",
        )


def test_local_environment_may_use_the_development_defaults() -> None:
    settings = _settings(ENVIRONMENT="local")

    assert settings.auth_provider == "mock"
    assert settings.jwt_secret.get_secret_value() == "dev-only-change-me"


def test_missing_required_setting_fails_loudly() -> None:
    """A missing DSN must fail at startup, not on the first query."""
    with pytest.raises(ValueError, match="database_url"):
        Settings(REDIS_URL="redis://cache:6379/0")


def test_secrets_are_not_rendered_in_repr() -> None:
    settings = _settings(JWT_SECRET="super-secret-value")

    assert "super-secret-value" not in repr(settings)
