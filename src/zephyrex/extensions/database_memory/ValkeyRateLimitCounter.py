# SPDX-License-Identifier: AGPL-3.0-or-later
"""Valkey-backed sliding-window rate-limit counter.

Implements the ``incr(key, window_seconds) -> int`` contract expected by
``InboundSecurity.set_rate_limit_counter()``. Uses a sorted set per key
with timestamps as scores; a Lua script atomically prunes expired entries,
adds the current timestamp, returns the count, and sets a GC-safety TTL.

Cross-process by design: every worker sharing the same Valkey instance
sees a unified view of rate-limit state, eliminating the per-process
split that ``_InMemoryCounter`` suffers under multi-worker uvicorn.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

_logger = logging.getLogger(__name__)

_LUA_INCR = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local cutoff = now - window

redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff)
redis.call('ZADD', key, now, now .. ':' .. math.random(1, 1000000))
local count = redis.call('ZCARD', key)
redis.call('EXPIRE', key, window + 1)
return count
"""


class ValkeyRateLimitCounter:
    """Cross-process sliding-window rate-limit counter backed by Valkey.

    The public API is synchronous (``incr`` / ``reset``) because the
    ``RateLimitMiddleware`` calls from a sync ASGI context. Internally
    the counter drives the async redis client via ``asyncio.run`` /
    ``loop.run_until_complete``, matching the pattern used by
    ``DistributedRateLimit._run_async``.
    """

    def __init__(self, client: Any, key_prefix: str = "rl:") -> None:
        self._client = client
        self._prefix = key_prefix
        self._script_sha: Optional[str] = None

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def _ensure_script(self) -> str:
        if self._script_sha is None:
            self._script_sha = await self._client.script_load(_LUA_INCR)
        return self._script_sha

    async def _async_incr(self, key: str, window_seconds: int) -> int:
        full_key = self._full_key(key)
        now = time.time()
        try:
            sha = await self._ensure_script()
            result = await self._client.evalsha(
                sha, 1, full_key, str(now), str(window_seconds)
            )
        except Exception:
            result = await self._client.eval(
                _LUA_INCR, 1, full_key, str(now), str(window_seconds)
            )
        return int(result)

    def incr(self, key: str, window_seconds: int) -> int:
        """Synchronous sliding-window increment. Returns post-increment count."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self._async_incr(key, window_seconds))
                return future.result(timeout=2)
        return asyncio.run(self._async_incr(key, window_seconds))

    async def _async_reset(self, key: Optional[str]) -> None:
        if key is None:
            async for k in self._client.scan_iter(match=f"{self._prefix}*"):
                await self._client.delete(k)
        else:
            await self._client.delete(self._full_key(key))

    def reset(self, key: Optional[str] = None) -> None:
        """Reset one or all rate-limit buckets."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, self._async_reset(key)).result(timeout=5)
        else:
            asyncio.run(self._async_reset(key))
