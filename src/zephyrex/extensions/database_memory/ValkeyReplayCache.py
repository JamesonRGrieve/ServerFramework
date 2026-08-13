# SPDX-License-Identifier: AGPL-3.0-or-later
"""Valkey-backed replay cache.

Subclass of ``lib.ReplayCache.ReplayCache`` that persists seen-nonce
state in Valkey. Cross-process by design: every worker sharing the same
Valkey instance sees a unified nonce set.

The critical ``mark_if_unused`` uses ``SET key 1 NX EX ttl`` — a single
atomic operation that combines the existence check and the mark into one
round-trip, eliminating the TOCTOU race that the in-memory default's
lock-guarded ``is_used`` + ``mark_used`` sequence cannot prevent across
processes (H-7).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from zephyrex.lib.ReplayCache import ReplayCache

_logger = logging.getLogger(__name__)


def _sync_run(coro):
    """Drive an async coroutine from sync code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result(timeout=2)
    return asyncio.run(coro)


class ValkeyReplayCache(ReplayCache):
    """Cross-process replay cache backed by Valkey."""

    def __init__(self, client: Any, prefix: str = "replay:") -> None:
        self._client = client
        self._prefix = prefix

    def _full_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def _async_mark_used(self, key: str, ttl_seconds: int) -> None:
        await self._client.set(self._full_key(key), b"1", ex=ttl_seconds)

    async def _async_is_used(self, key: str) -> bool:
        return bool(await self._client.exists(self._full_key(key)))

    async def _async_mark_if_unused(self, key: str, ttl_seconds: int) -> bool:
        result = await self._client.set(
            self._full_key(key), b"1", nx=True, ex=ttl_seconds
        )
        return result is not None and result is not False

    def mark_used(self, key: str, ttl_seconds: int) -> None:
        _sync_run(self._async_mark_used(key, ttl_seconds))

    def is_used(self, key: str) -> bool:
        return _sync_run(self._async_is_used(key))

    def mark_if_unused(self, key: str, ttl_seconds: int) -> bool:
        """Atomic CAS via ``SET NX EX`` — safe across processes (H-7).

        Fails closed on connection error: returns False (nonce rejected)
        so a Valkey outage cannot be exploited for replay attacks.
        """
        try:
            return _sync_run(self._async_mark_if_unused(key, ttl_seconds))
        except Exception as exc:
            _logger.warning("ValkeyReplayCache.mark_if_unused failed (fail-closed): %s", exc)
            return False
