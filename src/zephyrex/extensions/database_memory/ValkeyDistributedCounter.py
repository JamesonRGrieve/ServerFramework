# SPDX-License-Identifier: AGPL-3.0-or-later
"""Valkey-backed distributed counter (Item 69).

Subclass of ``lib.DistributedCounter.DistributedCounter`` using Valkey
``INCRBY`` with an atomic Lua script for limit enforcement. The script
atomically reads the current value, checks against the limit, increments,
and sets a TTL — all in one round-trip.

Cross-process by design: every worker sharing the same Valkey instance
sees unified counter state, replacing the per-process
``InMemoryDistributedCounter`` and the heavier
``PostgresDistributedCounter`` for deployments that already run Valkey.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from zephyrex.lib.DistributedCounter import DistributedCounter

_logger = logging.getLogger(__name__)

_LUA_TRY_CONSUME = """
local key = KEYS[1]
local amount = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])

local current = tonumber(redis.call('GET', key) or '0')
if current + amount > limit then
    return 0
end
redis.call('INCRBY', key, amount)
if ttl > 0 then
    redis.call('EXPIRE', key, ttl)
end
return 1
"""

_DEFAULT_TTL = 3600


class ValkeyDistributedCounter(DistributedCounter):
    """Valkey-backed distributed counter using atomic INCRBY + limit check."""

    def __init__(
        self,
        key: Optional[str] = None,
        limit: Optional[int] = None,
        period_key: Optional[str] = None,
        client: Any = None,
        ttl: int = _DEFAULT_TTL,
    ) -> None:
        super().__init__(key, limit, period_key)
        self._client = client
        self._ttl = ttl
        self._script_sha: Optional[str] = None

    @property
    def _valkey_key(self) -> str:
        if self.period_key:
            return f"dctr:{self.key}:{self.period_key}"
        return f"dctr:{self.key}"

    async def _ensure_script(self) -> str:
        if self._script_sha is None:
            self._script_sha = await self._client.script_load(_LUA_TRY_CONSUME)
        return self._script_sha

    async def _try_consume_one(self, amount: int) -> bool:
        if amount <= 0:
            return True
        if self._client is None:
            return True
        try:
            sha = await self._ensure_script()
            result = await self._client.evalsha(
                sha,
                1,
                self._valkey_key,
                str(amount),
                str(self.limit),
                str(self._ttl),
            )
        except Exception:
            result = await self._client.eval(
                _LUA_TRY_CONSUME,
                1,
                self._valkey_key,
                str(amount),
                str(self.limit),
                str(self._ttl),
            )
        return int(result) == 1

    async def release(self, amount: int) -> None:
        if amount <= 0 or self._client is None:
            return
        await self._client.decrby(self._valkey_key, amount)

    async def reset(self, period_key: str) -> None:
        if self._client is not None:
            old_key = self._valkey_key
            self.period_key = period_key
            await self._client.delete(old_key)
        else:
            self.period_key = period_key

    async def consumed(self) -> int:
        if self._client is None:
            return 0
        val = await self._client.get(self._valkey_key)
        return int(val) if val is not None else 0
