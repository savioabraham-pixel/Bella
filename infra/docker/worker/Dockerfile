# syntax=docker/dockerfile:1.11
#
# Bella worker — ARQ background tasks.
#
# The worker runs the same application package as the API (same models, same providers,
# same settings) but is a separate image on purpose:
#
#   * different dependencies — poppler and tesseract back document extraction and have no
#     business in the API image, which never parses a file
#   * different resource profile — CPU-heavy and bursty rather than I/O-bound
#   * different failure domain — a stuck extraction must not take request serving with it
#
# It deliberately does NOT build `FROM bella-api`. Chaining images means the two cannot
# build in parallel in CI, and a BuildKit builder with its own image store cannot resolve
# a locally-tagged parent at all.
#
# Build context is apps/api.

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.10.3

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ─── base ─────────────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

# poppler-utils  PDF rasterisation and text extraction
# tesseract-ocr  OCR for scanned documents, with the four languages Bella speaks —
#                this is what replaces the legacy "[PDF appears scanned]" dead end
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
 && apt-get install -y --no-install-recommends \
        libpq5 \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-hin \
        tesseract-ocr-ben \
        tesseract-ocr-mar \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1001 bella \
 && useradd --uid 1001 --gid bella --create-home --shell /usr/sbin/nologin bella

WORKDIR /app

# ─── deps ─────────────────────────────────────────────────────────────────────
FROM base AS deps

COPY --from=uv /uv /uvx /usr/local/bin/
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

COPY --chown=bella:bella . .

RUN mkdir -p /var/lib/bella/storage && chown -R bella:bella /var/lib/bella /app

USER bella

# No HTTP surface, so liveness is "the process can still reach the queue".
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import asyncio,os,redis.asyncio as r; asyncio.run(r.from_url(os.environ['REDIS_URL']).ping())" \
        || exit 1

CMD ["arq", "app.workers.main.WorkerSettings", "--watch", "/app/app"]

# ─── prod ─────────────────────────────────────────────────────────────────────
FROM base AS prod

ENV ENVIRONMENT=production \
    LOG_FORMAT=json

COPY --from=deps --chown=bella:bella /app/.venv /app/.venv
COPY --chown=bella:bella app ./app
COPY --chown=bella:bella pyproject.toml ./

RUN mkdir -p /var/lib/bella/storage && chown -R bella:bella /var/lib/bella

USER bella

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import asyncio,os,redis.asyncio as r; asyncio.run(r.from_url(os.environ['REDIS_URL']).ping())" \
        || exit 1

CMD ["arq", "app.workers.main.WorkerSettings"]
