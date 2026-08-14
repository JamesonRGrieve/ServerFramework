# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ValkeyReplayCache."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zephyrex.extensions.database_memory.ValkeyReplayCache import ValkeyReplayCache


def _make_mock_client():
    return AsyncMock()


class TestValkeyReplayCache:
    @pytest.mark.asyncio
    async def test_mark_used_calls_set_ex(self):
        client = _make_mock_client()
        cache = ValkeyReplayCache(client, prefix="replay:")

        await cache._async_mark_used("nonce123", 300)

        client.set.assert_awaited_once_with("replay:nonce123", b"1", ex=300)

    @pytest.mark.asyncio
    async def test_is_used_true(self):
        client = _make_mock_client()
        client.exists = AsyncMock(return_value=1)
        cache = ValkeyReplayCache(client)

        result = await cache._async_is_used("nonce123")

        assert result is True

    @pytest.mark.asyncio
    async def test_is_used_false(self):
        client = _make_mock_client()
        client.exists = AsyncMock(return_value=0)
        cache = ValkeyReplayCache(client)

        result = await cache._async_is_used("nonce123")

        assert result is False

    @pytest.mark.asyncio
    async def test_mark_if_unused_first_time(self):
        client = _make_mock_client()
        client.set = AsyncMock(return_value=True)
        cache = ValkeyReplayCache(client, prefix="r:")

        result = await cache._async_mark_if_unused("tok1", 120)

        assert result is True
        client.set.assert_awaited_once_with("r:tok1", b"1", nx=True, ex=120)

    @pytest.mark.asyncio
    async def test_mark_if_unused_already_used(self):
        client = _make_mock_client()
        client.set = AsyncMock(return_value=None)
        cache = ValkeyReplayCache(client)

        result = await cache._async_mark_if_unused("tok1", 120)

        assert result is False

    def test_is_subclass_of_replay_cache(self):
        from zephyrex.lib.ReplayCache import ReplayCache

        assert issubclass(ValkeyReplayCache, ReplayCache)

    def test_prefix_default(self):
        cache = ValkeyReplayCache(AsyncMock())
        assert cache._prefix == "replay:"
