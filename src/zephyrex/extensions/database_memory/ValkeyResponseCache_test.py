# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ValkeyResponseCache."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from zephyrex.extensions.database_memory.ValkeyResponseCache import (
    ValkeyResponseCache,
)


class TestValkeyResponseCache:
    def test_cache_key_deterministic(self):
        k1 = ValkeyResponseCache.cache_key("GET", "/v1/users", "page=1", "uid1")
        k2 = ValkeyResponseCache.cache_key("GET", "/v1/users", "page=1", "uid1")
        assert k1 == k2

    def test_cache_key_varies_on_user(self):
        k1 = ValkeyResponseCache.cache_key("GET", "/v1/users", "", "uid1")
        k2 = ValkeyResponseCache.cache_key("GET", "/v1/users", "", "uid2")
        assert k1 != k2

    def test_cache_key_anon(self):
        k = ValkeyResponseCache.cache_key("GET", "/v1/users", "", None)
        assert "resp:" in k

    def test_cache_key_path_prefix(self):
        k = ValkeyResponseCache.cache_key("GET", "/v1/users/123", "", None)
        assert k.startswith("resp:v1/users/123:")

    def test_etag_for(self):
        e1 = ValkeyResponseCache.etag_for('{"id": 1}')
        e2 = ValkeyResponseCache.etag_for('{"id": 1}')
        assert e1 == e2
        assert e1.startswith('"') and e1.endswith('"')

    def test_etag_differs_for_different_body(self):
        e1 = ValkeyResponseCache.etag_for('{"id": 1}')
        e2 = ValkeyResponseCache.etag_for('{"id": 2}')
        assert e1 != e2

    @pytest.mark.asyncio
    async def test_get_hit(self):
        client = AsyncMock()
        payload = {
            "status_code": 200,
            "body": '{"ok": true}',
            "headers": {},
            "etag": '"abc"',
        }
        client.get = AsyncMock(return_value=json.dumps(payload).encode())
        cache = ValkeyResponseCache(client)

        result = await cache.get("resp:test")

        assert result["status_code"] == 200
        assert result["body"] == '{"ok": true}'

    @pytest.mark.asyncio
    async def test_get_miss(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=None)
        cache = ValkeyResponseCache(client)

        result = await cache.get("resp:test")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_error_returns_none(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=ConnectionError("down"))
        cache = ValkeyResponseCache(client)

        result = await cache.get("resp:test")

        assert result is None

    @pytest.mark.asyncio
    async def test_put(self):
        client = AsyncMock()
        cache = ValkeyResponseCache(client, default_ttl=30)

        await cache.put("resp:k", 200, '{"ok":true}', {"X-Custom": "1"}, ttl=45)

        client.set.assert_awaited_once()
        call_args = client.set.call_args
        assert call_args.kwargs.get("ex") == 45 or call_args[1].get("ex") == 45

    @pytest.mark.asyncio
    async def test_put_default_ttl(self):
        client = AsyncMock()
        cache = ValkeyResponseCache(client, default_ttl=30)

        await cache.put("resp:k", 200, "{}", {})

        call_args = client.set.call_args
        assert call_args.kwargs.get("ex") == 30 or call_args[1].get("ex") == 30

    @pytest.mark.asyncio
    async def test_invalidate_prefix(self):
        client = AsyncMock()

        async def fake_scan(*args, **kwargs):
            for key in [b"resp:v1/users:abc", b"resp:v1/users:def"]:
                yield key

        client.scan_iter = fake_scan
        client.delete = AsyncMock()
        cache = ValkeyResponseCache(client)

        await cache.invalidate_prefix("/v1/users")

        assert client.delete.await_count == 2
