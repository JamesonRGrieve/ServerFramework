# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ValkeyRateLimitCounter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from zephyrex.extensions.database_memory.ValkeyRateLimitCounter import (
    ValkeyRateLimitCounter,
    _LUA_INCR,
)


def _make_mock_client():
    client = AsyncMock()
    client.script_load = AsyncMock(return_value="fake_sha")
    return client


class TestValkeyRateLimitCounter:
    def test_init(self):
        client = _make_mock_client()
        counter = ValkeyRateLimitCounter(client, key_prefix="test:")
        assert counter._prefix == "test:"
        assert counter._client is client

    def test_full_key(self):
        counter = ValkeyRateLimitCounter(AsyncMock(), key_prefix="rl:")
        assert counter._full_key("user:123") == "rl:user:123"

    @pytest.mark.asyncio
    async def test_async_incr_uses_evalsha(self):
        client = _make_mock_client()
        client.evalsha = AsyncMock(return_value=3)
        counter = ValkeyRateLimitCounter(client)

        result = await counter._async_incr("key1", 60)

        assert result == 3
        client.script_load.assert_awaited_once_with(_LUA_INCR)
        client.evalsha.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_incr_falls_back_to_eval(self):
        client = _make_mock_client()
        client.evalsha = AsyncMock(side_effect=Exception("NOSCRIPT"))
        client.eval = AsyncMock(return_value=1)
        counter = ValkeyRateLimitCounter(client)

        result = await counter._async_incr("key1", 60)

        assert result == 1
        client.eval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_async_reset_single_key(self):
        client = _make_mock_client()
        client.delete = AsyncMock()
        counter = ValkeyRateLimitCounter(client, key_prefix="rl:")

        await counter._async_reset("user:1")

        client.delete.assert_awaited_once_with("rl:user:1")

    @pytest.mark.asyncio
    async def test_async_reset_all_keys(self):
        client = _make_mock_client()
        client.scan_iter = AsyncMock()

        async def fake_scan(*args, **kwargs):
            for key in [b"rl:a", b"rl:b"]:
                yield key

        client.scan_iter = fake_scan
        client.delete = AsyncMock()
        counter = ValkeyRateLimitCounter(client, key_prefix="rl:")

        await counter._async_reset(None)

        assert client.delete.await_count == 2

    def test_incr_sync_wrapper_returns_count(self):
        client = _make_mock_client()
        client.evalsha = AsyncMock(return_value=4)
        counter = ValkeyRateLimitCounter(client, key_prefix="rl:")

        assert counter.incr("user:1", 60) == 4

    def test_incr_runs_under_running_event_loop(self):
        """The sync ``incr`` wrapper drives its coroutine via ``_cache_sync_run``
        on a worker thread when a loop is already running in the caller."""
        client = _make_mock_client()
        client.evalsha = AsyncMock(return_value=5)
        counter = ValkeyRateLimitCounter(client, key_prefix="rl:")

        async def _driver():
            return counter.incr("user:1", 60)

        assert asyncio.run(_driver()) == 5

    def test_reset_runs_under_running_event_loop(self):
        """The sync ``reset`` wrapper (wider 5s ceiling) also routes through the
        shared bridge when invoked from within a running loop."""
        client = _make_mock_client()
        client.delete = AsyncMock()
        counter = ValkeyRateLimitCounter(client, key_prefix="rl:")

        async def _driver() -> None:
            counter.reset("user:1")

        asyncio.run(_driver())

        client.delete.assert_awaited_once_with("rl:user:1")
