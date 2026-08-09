"""Redis client lifecycle.

Redis carries session revocation, rate-limit buckets, idempotency keys, distributed locks
and the SSE delta streams that make a dropped connection resumable. It is a hard dependency
of the chat path, so it is part of the readiness probe.
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import ConnectionPool, Redis

from app.core.config import Settings

# redis-py 5.3 ships py.typed, but its asyncio annotations do not describe the runtime:
# ConnectionPool is parameterised by connection type rather than value type, and
# `Redis(connection_pool=...)` narrows to Redis[bytes] regardless of the pool's
# decode_responses. Any is the honest annotation here rather than one that is precise
# and wrong.

_pool: ConnectionPool[Any] | None = None
_client: Redis[Any] | None = None


def init_redis(settings: Settings) -> Redis[Any]:
    global _pool, _client
    if _client is not None:
        return _client

    _pool = ConnectionPool.from_url(
        str(settings.redis_url),
        decode_responses=True,
        max_connections=50,
        health_check_interval=30,
        socket_keepalive=True,
        socket_connect_timeout=5,
    )
    _client = Redis(connection_pool=_pool)
    return _client


def get_redis() -> Redis[Any]:
    if _client is None:
        raise RuntimeError("Redis not initialised — call init_redis() at startup.")
    return _client


async def close_redis() -> None:
    global _pool, _client
    if _client is not None:
        # aclose() exists at runtime in redis>=5.0.1 and is the documented replacement
        # for the deprecated close(); it is simply missing from the shipped annotations.
        await _client.aclose()  # type: ignore[attr-defined]
    if _pool is not None:
        await _pool.disconnect()
    _client = None
    _pool = None


async def check_redis() -> bool:
    return bool(await get_redis().ping())
