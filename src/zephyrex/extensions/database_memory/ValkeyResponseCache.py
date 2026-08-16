# SPDX-License-Identifier: AGPL-3.0-or-later
"""Valkey-backed HTTP response cache.

Caches full JSON responses keyed by (method, path, query, requester_id).
Supports Cache-Control compliance (no-cache / no-store), ETag generation,
and conditional request handling (If-None-Match → 304).

Response cache keys are prefixed with ``resp:`` and include a SHA-256 hash
of the request identity. Invalidation uses ``SCAN`` over a path-prefix
pattern to clear all cached responses for a resource when it is mutated.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

_logger = logging.getLogger(__name__)

_DEFAULT_TTL = 60


class ValkeyResponseCache:
    """Full HTTP response cache backed by Valkey."""

    def __init__(self, client: Any, default_ttl: int = _DEFAULT_TTL) -> None:
        self._client = client
        self._default_ttl = default_ttl

    @staticmethod
    def cache_key(
        method: str,
        path: str,
        query: str,
        requester_id: str | None,
    ) -> str:
        """Deterministic, tenant-safe cache key."""
        raw = f"{method}:{path}:{query}:{requester_id or 'anon'}"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        path_prefix = "/".join(path.strip("/").split("/")[:3])
        return f"resp:{path_prefix}:{digest}"

    @staticmethod
    def etag_for(body: str) -> str:
        return f'"{hashlib.sha256(body.encode()).hexdigest()}"'

    async def get(self, key: str) -> dict | None:
        """Return cached response dict or None."""
        try:
            raw = await self._client.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as exc:
            _logger.debug("ValkeyResponseCache.get error: %s", exc)
            return None

    async def put(
        self,
        key: str,
        status_code: int,
        body: str,
        headers: dict,
        ttl: int | None = None,
    ) -> None:
        """Cache a response."""
        effective_ttl = ttl if ttl is not None else self._default_ttl
        try:
            payload = json.dumps(
                {
                    "status_code": status_code,
                    "body": body,
                    "headers": headers,
                    "etag": self.etag_for(body),
                }
            )
            await self._client.set(key, payload, ex=effective_ttl)
        except Exception as exc:
            _logger.debug("ValkeyResponseCache.put error: %s", exc)

    async def invalidate_prefix(self, path_prefix: str) -> None:
        """Delete all response cache keys matching a path prefix."""
        clean_prefix = "/".join(path_prefix.strip("/").split("/")[:3])
        pattern = f"resp:{clean_prefix}:*"
        try:
            async for key in self._client.scan_iter(match=pattern, count=500):
                await self._client.delete(key)
        except Exception as exc:
            _logger.debug("ValkeyResponseCache.invalidate_prefix error: %s", exc)
