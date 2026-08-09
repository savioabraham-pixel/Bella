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


def test_is_production_flag() -> None:
    assert _settings(ENVIRONMENT="production").is_production is True
    assert _settings(ENVIRONMENT="local").is_production is False


def test_missing_required_setting_fails_loudly() -> None:
    """A missing DSN must fail at startup, not on the first query."""
    with pytest.raises(ValueError, match="database_url"):
        Settings(REDIS_URL="redis://cache:6379/0")  # type: ignore[call-arg]


def test_secrets_are_not_rendered_in_repr() -> None:
    settings = _settings(JWT_SECRET="super-secret-value")

    assert "super-secret-value" not in repr(settings)
