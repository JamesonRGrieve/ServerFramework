# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ValkeyEntityCache."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zephyrex.extensions.database_memory.ValkeyEntityCache import ValkeyEntityCache


def _make_mock_client():
    client = AsyncMock()
    pipe = AsyncMock()
    pipe.set = MagicMock()
    pipe.execute = AsyncMock(return_value=[True])
    client.pipeline = MagicMock(return_value=pipe)
    return client, pipe


class TestValkeyEntityCache:
    def test_entity_key(self):
        assert ValkeyEntityCache._entity_key("user", "abc") == "entity:user:abc"

    def test_index_key(self):
        assert (
            ValkeyEntityCache._index_key("user", "email", "a@b.com")
            == "idx:user:email:a@b.com"
        )

    def test_configure_entity_ttl(self):
        cache = ValkeyEntityCache(AsyncMock())
        cache.configure_entity("user", ttl=600)
        assert cache._ttl_for("user") == 600
        assert cache._ttl_for("other") == 300

    def test_configure_entity_index_fields(self):
        cache = ValkeyEntityCache(AsyncMock())
        cache.configure_entity("user", index_fields={"email", "username"})
        assert cache._index_fields["user"] == {"email", "username"}

    @patch.dict("os.environ", {"VALKEY_ENTITY_TTL_USER": "900"})
    def test_configure_entity_env_override(self):
        cache = ValkeyEntityCache(AsyncMock())
        cache.configure_entity("user", ttl=600)
        assert cache._ttl_for("user") == 900

    @pytest.mark.asyncio
    async def test_get_by_id_hit(self):
        client = AsyncMock()
        dto = {"id": "123", "name": "Alice"}
        client.get = AsyncMock(return_value=json.dumps(dto).encode())
        cache = ValkeyEntityCache(client)

        result = await cache.get_by_id("user", "123")

        assert result == dto
        client.get.assert_awaited_once_with("entity:user:123")

    @pytest.mark.asyncio
    async def test_get_by_id_miss(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=None)
        cache = ValkeyEntityCache(client)

        result = await cache.get_by_id("user", "456")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_id_error_returns_none(self):
        client = AsyncMock()
        client.get = AsyncMock(side_effect=ConnectionError("down"))
        cache = ValkeyEntityCache(client)

        result = await cache.get_by_id("user", "123")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_field(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=b"uuid-123")
        cache = ValkeyEntityCache(client)

        result = await cache.get_by_field("user", "email", "a@b.com")

        assert result == "uuid-123"
        client.get.assert_awaited_once_with("idx:user:email:a@b.com")

    @pytest.mark.asyncio
    async def test_put_with_index_fields(self):
        client, pipe = _make_mock_client()
        cache = ValkeyEntityCache(client, default_ttl=120)

        await cache.put(
            "user", "id1", {"id": "id1", "email": "a@b.com"}, {"email": "a@b.com"}
        )

        client.pipeline.assert_called_once_with(transaction=False)
        assert pipe.set.call_count == 2
        pipe.execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_put_without_index(self):
        client, pipe = _make_mock_client()
        cache = ValkeyEntityCache(client)

        await cache.put("team", "t1", {"id": "t1", "name": "Team A"})

        assert pipe.set.call_count == 1

    @pytest.mark.asyncio
    async def test_invalidate(self):
        client = AsyncMock()
        cache = ValkeyEntityCache(client)

        await cache.invalidate("user", "id1", {"email": "old@b.com"})

        client.delete.assert_awaited_once_with(
            "entity:user:id1", "idx:user:email:old@b.com"
        )

    @pytest.mark.asyncio
    async def test_invalidate_no_index(self):
        client = AsyncMock()
        cache = ValkeyEntityCache(client)

        await cache.invalidate("user", "id1")

        client.delete.assert_awaited_once_with("entity:user:id1")

    @pytest.mark.asyncio
    async def test_invalidate_error_logs_warning(self):
        client = AsyncMock()
        client.delete = AsyncMock(side_effect=ConnectionError("down"))
        cache = ValkeyEntityCache(client)

        await cache.invalidate("user", "id1")

    @pytest.mark.asyncio
    async def test_invalidate_table(self):
        client = AsyncMock()

        async def fake_scan(*args, **kwargs):
            for key in [b"entity:user:1", b"entity:user:2"]:
                yield key

        client.scan_iter = fake_scan
        client.delete = AsyncMock()
        cache = ValkeyEntityCache(client)

        await cache.invalidate_table("user")

        assert client.delete.await_count >= 2

    @pytest.mark.asyncio
    async def test_get_by_field_full_hit(self):
        client = AsyncMock()
        dto = {"id": "u1", "email": "a@b.com"}
        client.get = AsyncMock(
            side_effect=[
                b"u1",
                json.dumps(dto).encode(),
            ]
        )
        cache = ValkeyEntityCache(client)

        result = await cache.get_by_field_full("user", "email", "a@b.com")

        assert result == dto
        assert client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_get_by_field_full_index_miss(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=None)
        cache = ValkeyEntityCache(client)

        result = await cache.get_by_field_full("user", "email", "nonexistent")

        assert result is None
