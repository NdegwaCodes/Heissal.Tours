"""Async Redis client (cache, refresh-token revocation deny-list, rate limiting)."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import cast

import redis.asyncio as aioredis

from app.core.config import settings

redis_client: aioredis.Redis = aioredis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True,
)


async def get_redis() -> aioredis.Redis:
    return redis_client


async def ping() -> bool:
    """Ping Redis.

    redis-py annotates ``ping()`` as ``Awaitable[bool] | bool`` (the client is
    generic over sync/async), so the await needs a cast to type-check.
    """
    return bool(await cast(Awaitable[bool], redis_client.ping()))
