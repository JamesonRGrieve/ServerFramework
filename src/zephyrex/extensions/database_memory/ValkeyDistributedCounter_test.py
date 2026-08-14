# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for ValkeyDistributedCounter."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zephyrex.extensions.database_memory.ValkeyDistributedCounter import (
    ValkeyDistributedCounter,
)
from zephyrex.lib.DistributedCounter import DistributedCounter


class TestValkeyDistributedCounter:
    def test_is_subclass(self):
        assert issubclass(ValkeyDistributedCounter, DistributedCounter)

    def test_valkey_key_without_period(self):
        c = ValkeyDistributedCounter(key="test", limit=100)
        assert c._valkey_key == "dctr:test"

    def test_valkey_key_with_period(self):
        c = ValkeyDistributedCounter(key="test", limit=100, period_key="2024-01")
        assert c._valkey_key == "dctr:test:2024-01"

    @pytest.mark.asyncio
    async def test_try_consume_success(self):
        client = AsyncMock()
        client.script_load = AsyncMock(return_value="sha")
        client.evalsha = AsyncMock(return_value=1)

        c = ValkeyDistributedCounter(key="k", limit=10, client=client)
        result = await c._try_consume_one(1)

        assert result is True
        client.evalsha.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_try_consume_exhausted(self):
        client = AsyncMock()
        client.script_load = AsyncMock(return_value="sha")
        client.evalsha = AsyncMock(return_value=0)

        c = ValkeyDistributedCounter(key="k", limit=10, client=client)
        result = await c._try_consume_one(1)

        assert result is False

    @pytest.mark.asyncio
    async def test_try_consume_zero_amount(self):
        c = ValkeyDistributedCounter(key="k", limit=10, client=AsyncMock())
        assert await c._try_consume_one(0) is True

    @pytest.mark.asyncio
    async def test_try_consume_no_client(self):
        c = ValkeyDistributedCounter(key="k", limit=10, client=None)
        assert await c._try_consume_one(5) is True

    @pytest.mark.asyncio
    async def test_release(self):
        client = AsyncMock()
        c = ValkeyDistributedCounter(key="k", limit=10, client=client)

        await c.release(3)

        client.decrby.assert_awaited_once_with("dctr:k", 3)

    @pytest.mark.asyncio
    async def test_release_zero(self):
        client = AsyncMock()
        c = ValkeyDistributedCounter(key="k", limit=10, client=client)

        await c.release(0)

        client.decrby.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_reset(self):
        client = AsyncMock()
        c = ValkeyDistributedCounter(key="k", limit=10, client=client)

        await c.reset("2024-02")

        client.delete.assert_awaited_once_with("dctr:k")
        assert c.period_key == "2024-02"

    @pytest.mark.asyncio
    async def test_consumed(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=b"42")
        c = ValkeyDistributedCounter(key="k", limit=10, client=client)

        result = await c.consumed()

        assert result == 42

    @pytest.mark.asyncio
    async def test_consumed_no_key(self):
        client = AsyncMock()
        client.get = AsyncMock(return_value=None)
        c = ValkeyDistributedCounter(key="k", limit=10, client=client)

        result = await c.consumed()

        assert result == 0

    @pytest.mark.asyncio
    async def test_evalsha_fallback_to_eval(self):
        client = AsyncMock()
        client.script_load = AsyncMock(return_value="sha")
        client.evalsha = AsyncMock(side_effect=Exception("NOSCRIPT"))
        client.eval = AsyncMock(return_value=1)

        c = ValkeyDistributedCounter(key="k", limit=10, client=client)
        result = await c._try_consume_one(1)

        assert result is True
        client.eval.assert_awaited_once()
