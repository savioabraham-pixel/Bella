# syntax=docker/dockerfile:1.11
#
# Bella API — multi-stage build.
#
#   base    shared runtime layer, non-root user, no build tools
#   deps    dependency resolution only, cached on the lockfile
#   dev     + dev dependencies, source bind-mounted at runtime, hot reload
#   prod    source baked in, read-only friendly, gunicorn + uvicorn workers
#
# Build context is apps/api. Target with:  docker build --target dev -f this .

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.10.3

# uv is pulled in as a named stage: a global ARG is only expanded in FROM lines, so
# referencing it directly from a COPY --from inside a stage would resolve to an empty tag.
FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ─── base ─────────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

# curl is used by the container healthcheck; libpq for asyncpg's runtime needs.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
 && apt-get install -y --no-install-recommends \
        curl \
        libpq5 \
 && rm -rf /var/lib/apt/lists/*

# Non-root from the start. UID is fixed so bind-mounted files keep sane ownership.
RUN groupadd --gid 1001 bella \
 && useradd --uid 1001 --gid bella --create-home --shell /usr/sbin/nologin bella

WORKDIR /app

# ─── deps ─────────────────────────────────────────────────────────────────────
FROM base AS deps

COPY --from=uv /uv /uvx /usr/local/bin/

# Resolve dependencies against the manifest alone, so editing source code does not
# invalidate this layer.
COPY pyproject.toml ./
COPY uv.lock* ./

RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -f uv.lock ]; then \
        uv sync --frozen --no-install-project --no-dev; \
    else \
        uv sync --no-install-project --no-dev; \
    fi

# ─── dev ──────────────────────────────────────────────────────────────────────
FROM deps AS dev

ENV ENVIRONMENT=local \
    LOG_FORMAT=console

RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -f uv.lock ]; then \
        uv sync --frozen --no-install-project; \
    else \
        uv sync --no-install-project; \
    fi

# Source is bind-mounted by compose in dev; this copy keeps the image runnable standalone.
COPY --chown=bella:bella . .

RUN mkdir -p /var/lib/bella/storage && chown -R bella:bella /var/lib/bella /app

USER bella
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD curl --fail --silent http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", \
     "--reload", "--reload-dir", "/app/app", \
     "--log-level", "info"]

# ─── prod ─────────────────────────────────────────────────────────────────────
FROM base AS prod

ENV ENVIRONMENT=production \
    LOG_FORMAT=json \
    WEB_CONCURRENCY=4

# Only the resolved virtualenv crosses over — uv and the build cache stay behind.
COPY --from=deps --chown=bella:bella /app/.venv /app/.venv
COPY --chown=bella:bella app ./app
COPY --chown=bella:bella alembic.ini ./
COPY --chown=bella:bella pyproject.toml ./

RUN mkdir -p /var/lib/bella/storage && chown -R bella:bella /var/lib/bella

USER bella
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD curl --fail --silent http://localhost:8000/health || exit 1

# Gunicorn supervises; uvicorn workers do the serving. Cloud Run terminates TLS and
# provides its own timeout, so keep-alive is short and the app timeout generous enough
# for a streaming response to finish.
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "120", \
     "--graceful-timeout", "30", \
     "--keep-alive", "5", \
     "--max-requests", "10000", \
     "--max-requests-jitter", "1000", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
