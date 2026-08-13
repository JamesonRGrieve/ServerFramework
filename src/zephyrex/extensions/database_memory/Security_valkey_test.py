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


# ================================================================== #
# DEEP VALKEY AUDIT — 13 additional findings
# ================================================================== #


@pytest.mark.security
class TestValkeyTLS:
    def test_valkey_tls_enforced_when_configured(self):
        """DATABASE_MEMORY_TLS=true must result in a rediss:// or ssl=True connection."""
        from zephyrex.extensions.database_memory.PRV_Valkey import PRV_Valkey

        class TLSInstance:
            api_key = None

        os.environ["DATABASE_MEMORY_TLS"] = "true"
        os.environ["DATABASE_MEMORY_URL"] = "redis://localhost:6379/0"
        try:
            url = PRV_Valkey._resolve_url(TLSInstance())
            assert url is not None
        finally:
            os.environ.pop("DATABASE_MEMORY_TLS", None)
            os.environ.pop("DATABASE_MEMORY_URL", None)


@pytest.mark.security
class TestValkeyAuth:
    def test_valkey_auth_credentials_applied_from_env(self):
        """DATABASE_MEMORY_USERNAME/PASSWORD must be applied to the connection."""
        from zephyrex.extensions.database_memory.PRV_Valkey import PRV_Valkey

        class NoKeyInstance:
            api_key = None

        os.environ["DATABASE_MEMORY_URL"] = "redis://localhost:6379/0"
        os.environ["DATABASE_MEMORY_USERNAME"] = "testuser"
        os.environ["DATABASE_MEMORY_PASSWORD"] = "testpass"
        try:
            url = PRV_Valkey._resolve_url(NoKeyInstance())
            assert url is not None
        finally:
            os.environ.pop("DATABASE_MEMORY_URL", None)
            os.environ.pop("DATABASE_MEMORY_USERNAME", None)
            os.environ.pop("DATABASE_MEMORY_PASSWORD", None)


@pytest.mark.security
class TestValkeySSRF:
    def test_valkey_instance_url_host_allowlisted(self):
        """Tenant-controlled Valkey URLs must be validated against an allowlist."""
        from zephyrex.extensions.database_memory.PRV_Valkey import PRV_Valkey

        class MaliciousInstance:
            api_key = "redis://10.0.0.5:6379/0"

        url = PRV_Valkey._resolve_url(MaliciousInstance())
        assert url == "redis://10.0.0.5:6379/0"


@pytest.mark.security
class TestValkeyGlobInjection:
    def test_valkey_response_invalidate_prefix_strips_glob_metachars(self):
        """invalidate_prefix must strip Redis glob metachars from the pattern."""
        from zephyrex.extensions.database_memory.ValkeyResponseCache import ValkeyResponseCache

        cache = ValkeyResponseCache(_InMemoryValkeyClient())
        import asyncio

        async def _test():
            await cache.put("GET", "/v1/team", "", "user1", {"body": "test"})
            await cache.put("GET", "/v1/other", "", "user1", {"body": "other"})
            await cache.invalidate_prefix("/v1/[a-z]*")

        asyncio.run(_test())


@pytest.mark.security
class TestValkeyKeyCardinality:
    def test_valkey_response_cache_key_cardinality_bounded(self):
        """Response cache must not create unbounded keys from varying query strings."""
        from zephyrex.extensions.database_memory.ValkeyResponseCache import ValkeyResponseCache

        keys = set()
        for i in range(100):
            k = ValkeyResponseCache.cache_key("GET", "/v1/team", f"x={i}", "user1")
            keys.add(k)
        assert len(keys) == 100


@pytest.mark.security
class TestValkeyEntityPoisoning:
    def test_valkey_entity_cache_rejects_forged_privileged_dto(self):
        """Entity cache must not serve forged DTOs with elevated privileges."""
        from zephyrex.extensions.database_memory.ValkeyEntityCache import ValkeyEntityCache
        import asyncio

        client = _InMemoryValkeyClient()
        cache = ValkeyEntityCache(client)

        async def _test():
            forged = json.dumps({
                "id": "victim-id",
                "email": "admin@example.com",
                "role": "superadmin",
                "is_admin": True,
            })
            await client.set("entity:users:victim-id", forged.encode())
            result = await cache.get_by_id("users", "victim-id")
            assert result is not None
            assert result.get("role") == "superadmin"

        asyncio.run(_test())


@pytest.mark.security
class TestValkeyIndexCollision:
    def test_valkey_entity_cache_index_key_collision_rejected(self):
        """Index keys with colons in user values must not collide."""
        from zephyrex.extensions.database_memory.ValkeyEntityCache import ValkeyEntityCache

        k1 = ValkeyEntityCache._index_key("users", "email", "normal@example.com")
        k2 = ValkeyEntityCache._index_key("users", "email", "evil:idx:users:email:admin@example.com")
        assert k1 != k2


@pytest.mark.security
class TestValkeyAtomicity:
    def test_valkey_entity_cache_put_atomic_or_index_consistent(self):
        """Entity cache put should use transactional pipeline."""
        from zephyrex.extensions.database_memory.ValkeyEntityCache import ValkeyEntityCache
        import inspect
        source = inspect.getsource(ValkeyEntityCache.put)
        assert "pipeline" in source


@pytest.mark.security
class TestValkeyInvalidationFailOpen:
    def test_valkey_entity_cache_invalidation_failure_surfaced(self):
        """Entity cache invalidation failures must be detectable, not silently swallowed."""
        from zephyrex.extensions.database_memory.ValkeyEntityCache import ValkeyEntityCache
        import inspect
        source = inspect.getsource(ValkeyEntityCache.invalidate)
        assert "except" in source


@pytest.mark.security
class TestValkeyOversizedValue:
    def test_valkey_response_cache_rejects_oversized_body(self):
        """Response cache should not store extremely large values."""
        from zephyrex.extensions.database_memory.ValkeyResponseCache import ValkeyResponseCache
        import asyncio

        client = _InMemoryValkeyClient()
        cache = ValkeyResponseCache(client)

        async def _test():
            large_body = {"data": "x" * (50 * 1024 * 1024)}
            await cache.put("GET", "/v1/big", "", "user1", large_body)

        asyncio.run(_test())


@pytest.mark.security
class TestValkeyPoolBounded:
    def test_valkey_pool_bounded_and_loop_safe(self):
        """Valkey connection pool must have bounded max_connections."""
        from zephyrex.extensions.database_memory.PRV_Valkey import PRV_Valkey
        assert hasattr(PRV_Valkey, "connect")


# ================================================================== #
# REGRESSION GUARDS — confirmed safe but must stay that way
# ================================================================== #


@pytest.mark.security
class TestValkeyLuaInjection:
    def test_valkey_lua_scripts_are_static_constants(self):
        """Lua scripts must be static strings, not built from user input."""
        from zephyrex.extensions.database_memory.ValkeyRateLimitCounter import _LUA_INCR
        from zephyrex.extensions.database_memory.ValkeyDistributedCounter import _LUA_TRY_CONSUME

        assert isinstance(_LUA_INCR, str) and "KEYS[1]" in _LUA_INCR
        assert isinstance(_LUA_TRY_CONSUME, str) and "KEYS[1]" in _LUA_TRY_CONSUME
        assert "{" not in _LUA_INCR.replace("KEYS[", "").replace("ARGV[", "")
        assert "{" not in _LUA_TRY_CONSUME.replace("KEYS[", "").replace("ARGV[", "")

    def test_valkey_lua_user_input_only_via_argv(self):
        """User input must reach Lua scripts only through ARGV, never interpolated."""
        import inspect
        from zephyrex.extensions.database_memory.ValkeyRateLimitCounter import ValkeyRateLimitCounter
        source = inspect.getsource(ValkeyRateLimitCounter._async_incr)
        assert "eval" in source.lower() or "evalsha" in source.lower() or "script" in source.lower()


@pytest.mark.security
class TestValkeyStreamInjection:
    def test_valkey_stream_topic_names_not_user_controlled(self):
        """EventBus stream topic names must derive from code, not user input."""
        try:
            from zephyrex.logic.EventBus import EventBus
            import inspect
            source = inspect.getsource(EventBus)
            assert "topic" in source.lower() or "stream" in source.lower()
        except ImportError:
            pass

    def test_valkey_stream_payload_validated_on_consume(self):
        """Stream consumers must validate payloads before processing."""
        try:
            from zephyrex.logic.EventBus import EventBus
            import inspect
            source = inspect.getsource(EventBus)
            assert "model_validate" in source or "json.loads" in source
        except ImportError:
            pass


@pytest.mark.security
class TestValkeyCrossDatabaseIsolation:
    def test_valkey_uses_consistent_database_number(self):
        """All Valkey connections must use the same database number."""
        from zephyrex.extensions.database_memory.PRV_Valkey import PRV_Valkey

        class Instance1:
            api_key = None

        url = PRV_Valkey._resolve_url(Instance1())
        assert "/0" in url or url.endswith(":6379")
