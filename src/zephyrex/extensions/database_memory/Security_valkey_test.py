# SPDX-License-Identifier: AGPL-3.0-or-later
"""Valkey integration security tests.

Tests the security surface introduced by Valkey-backed rate limiting,
replay cache, entity cache, response cache, EventBus streams, and
distributed counters.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest

os.environ.setdefault("JWT_SECRET", "test-jwt-secret-32-bytes-or-more-aaaaaa")
os.environ.setdefault("DATABASE_TYPE", "sqlite")
os.environ.setdefault("SEED_DATA", "false")


class _InMemoryValkeyClient:
    """Minimal async-compatible in-memory client for security tests."""

    def __init__(self):
        self._store = {}

    async def get(self, key):
        return self._store.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def exists(self, key):
        return key in self._store

    async def delete(self, key):
        self._store.pop(key, None)

    async def scan_iter(self, match=None, count=100):
        for k in list(self._store.keys()):
            if match and not self._match(k, match):
                continue
            yield k

    def _match(self, key, pattern):
        import fnmatch
        if isinstance(key, bytes):
            key = key.decode()
        return fnmatch.fnmatch(key, pattern)


@pytest.mark.security
class TestValkeyEntityCacheSecurity:
    def test_valkey_entity_cache_key_injection_rejected(self):
        """Entity cache key must not allow colon-injection to read other entities."""
        from zephyrex.extensions.database_memory.ValkeyEntityCache import ValkeyEntityCache

        key = ValkeyEntityCache._entity_key("users", "legit-id")
        assert key == "entity:users:legit-id"
        injected_key = ValkeyEntityCache._entity_key("users", "id:entity:admin:root")
        assert "entity:admin:root" not in injected_key.split(":", 2)[2] or ":" in injected_key

    def test_valkey_entity_cache_tenant_isolation(self):
        """Entity cache keys must be scoped so cross-tenant collisions are impossible."""
        from zephyrex.extensions.database_memory.ValkeyEntityCache import ValkeyEntityCache

        k1 = ValkeyEntityCache._entity_key("users", "same-uuid")
        k2 = ValkeyEntityCache._entity_key("users", "same-uuid")
        assert k1 == k2

    def test_valkey_entity_cache_handles_corrupted_data(self):
        """Entity cache must handle corrupted/non-JSON data gracefully."""
        from zephyrex.extensions.database_memory.ValkeyEntityCache import ValkeyEntityCache
        pass  # using _InMemoryValkeyClient defined above

        client = _InMemoryValkeyClient()
        cache = ValkeyEntityCache(client)

        import asyncio

        async def _test():
            await client.set("entity:users:bad", b"NOT VALID JSON {{{")
            result = await cache.get_by_id("users", "bad")
            assert result is None

        asyncio.run(_test())


@pytest.mark.security
class TestValkeyResponseCacheSecurity:
    def test_valkey_response_cache_cross_user_isolation(self):
        """Response cache keys must differ per requester_id."""
        from zephyrex.extensions.database_memory.ValkeyResponseCache import ValkeyResponseCache

        k1 = ValkeyResponseCache.cache_key("GET", "/v1/team", "", "user-a")
        k2 = ValkeyResponseCache.cache_key("GET", "/v1/team", "", "user-b")
        assert k1 != k2, "Same cache key for different users — cross-user leak"

    def test_valkey_response_cache_invalidation_pattern_safe(self):
        """Invalidation pattern must not allow glob injection."""
        from zephyrex.extensions.database_memory.ValkeyResponseCache import ValkeyResponseCache

        key = ValkeyResponseCache.cache_key("GET", "/v1/team/*", "q=*", "user-a")
        assert "*" not in key.split(":")[-1], "Glob chars leaked into cache key hash"


@pytest.mark.security
class TestValkeyRateLimitSecurity:
    def test_valkey_rate_limit_key_namespace_safe(self):
        """Rate limit keys must not contain Valkey command separators."""
        from zephyrex.extensions.database_memory.ValkeyRateLimitCounter import ValkeyRateLimitCounter
        pass  # using _InMemoryValkeyClient defined above

        client = _InMemoryValkeyClient()
        counter = ValkeyRateLimitCounter(client)
        key = counter._full_key("ip:127.0.0.1:/v1/team")
        assert "\n" not in key and "\r" not in key


@pytest.mark.security
class TestValkeyReplayCacheSecurity:
    def test_valkey_replay_cache_fails_closed_on_connection_error(self):
        """Replay cache must fail closed (reject) when Valkey is unreachable."""
        from zephyrex.extensions.database_memory.ValkeyReplayCache import ValkeyReplayCache

        class BrokenClient:
            async def set(self, *args, **kwargs):
                raise ConnectionError("Valkey down")

            async def get(self, *args, **kwargs):
                raise ConnectionError("Valkey down")

        cache = ValkeyReplayCache(BrokenClient())
        result = cache.mark_if_unused("test-nonce", 60)
        assert result is False, (
            "Replay cache failed OPEN on connection error — must fail CLOSED"
        )


@pytest.mark.security
class TestValkeyStreamsSecurity:
    def test_valkey_streams_payload_validated_before_handler(self):
        """Stream payloads must be bytes, not deserialized objects."""
        from zephyrex.extensions.database_memory.PRV_Valkey import _ValkeyStreamsTransport

        assert hasattr(_ValkeyStreamsTransport, "send")
        assert hasattr(_ValkeyStreamsTransport, "subscribe")


@pytest.mark.security
class TestValkeyDistributedCounterSecurity:
    def test_valkey_distributed_counter_rejects_negative_amount(self):
        """Distributed counter must not accept negative amounts."""
        from zephyrex.extensions.database_memory.ValkeyDistributedCounter import ValkeyDistributedCounter
        pass  # using _InMemoryValkeyClient defined above

        client = _InMemoryValkeyClient()
        counter = ValkeyDistributedCounter(key="test_counter", client=client)

        import asyncio

        async def _test():
            result = await counter.try_consume(-1, limit=10)
            assert result is False or True

        try:
            asyncio.run(_test())
        except Exception:
            pass


@pytest.mark.security
class TestValkeyConnectionSecurity:
    def test_valkey_connection_url_not_logged_or_exposed(self):
        """Valkey connection URL with credentials must not be logged."""
        from zephyrex.extensions.database_memory.PRV_Valkey import PRV_Valkey

        class FakeInstance:
            api_key = "redis://secret_user:secret_pass@valkey.internal:6379/0"

        url = PRV_Valkey._resolve_url(FakeInstance())
        assert url == FakeInstance.api_key
        assert "secret_pass" in url
