# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for EXT_DatabaseMemory.wire_framework_backends()."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zephyrex.extensions.database_memory.EXT_DatabaseMemory import (
    EXT_DatabaseMemory,
)


def _make_mock_client():
    client = AsyncMock()
    pipe = AsyncMock()
    pipe.set = MagicMock()
    pipe.execute = AsyncMock(return_value=[True])
    client.pipeline = MagicMock(return_value=pipe)
    return client


class TestWireFrameworkBackends:
    def test_wires_rate_limit_counter(self):
        client = _make_mock_client()
        with patch("zephyrex.lib.InboundSecurity.set_rate_limit_counter") as mock_set:
            EXT_DatabaseMemory.wire_framework_backends(client)

        mock_set.assert_called_once()
        from zephyrex.extensions.database_memory.ValkeyRateLimitCounter import (
            ValkeyRateLimitCounter,
        )

        assert isinstance(mock_set.call_args[0][0], ValkeyRateLimitCounter)

    def test_wires_replay_cache(self):
        client = _make_mock_client()
        with patch("zephyrex.lib.ReplayCache.set_replay_cache") as mock_set:
            EXT_DatabaseMemory.wire_framework_backends(client)

        mock_set.assert_called_once()
        from zephyrex.extensions.database_memory.ValkeyReplayCache import (
            ValkeyReplayCache,
        )

        assert isinstance(mock_set.call_args[0][0], ValkeyReplayCache)

    def test_wires_entity_cache(self):
        client = _make_mock_client()
        with patch("zephyrex.logic.AbstractLogicManager.set_entity_cache") as mock_set:
            EXT_DatabaseMemory.wire_framework_backends(client)

        mock_set.assert_called_once()
        from zephyrex.extensions.database_memory.ValkeyEntityCache import (
            ValkeyEntityCache,
        )

        assert isinstance(mock_set.call_args[0][0], ValkeyEntityCache)

    def test_wires_response_cache(self):
        client = _make_mock_client()
        with patch("zephyrex.lib.ResponseCache.set_response_cache") as mock_set:
            EXT_DatabaseMemory.wire_framework_backends(client)

        mock_set.assert_called_once()
        from zephyrex.extensions.database_memory.ValkeyResponseCache import (
            ValkeyResponseCache,
        )

        assert isinstance(mock_set.call_args[0][0], ValkeyResponseCache)

    def test_wires_event_bus_when_inmemory(self):
        client = _make_mock_client()
        from zephyrex.logic.EventBus import (
            InMemoryEventBus,
            get_event_bus,
            set_event_bus,
        )

        original_bus = get_event_bus()
        set_event_bus(InMemoryEventBus())

        try:
            EXT_DatabaseMemory.wire_framework_backends(client)
            from zephyrex.logic.EventBus import RedisStreamsEventBus

            assert isinstance(get_event_bus(), RedisStreamsEventBus)
        finally:
            set_event_bus(original_bus)

    def test_does_not_replace_non_inmemory_bus(self):
        client = _make_mock_client()
        from zephyrex.logic.EventBus import (
            RedisStreamsEventBus,
            get_event_bus,
            set_event_bus,
        )
        from zephyrex.logic.EventBus import InMemoryBrokerTransport

        custom_bus = RedisStreamsEventBus(transport=InMemoryBrokerTransport())
        set_event_bus(custom_bus)

        try:
            EXT_DatabaseMemory.wire_framework_backends(client)
            assert get_event_bus() is custom_bus
        finally:
            from zephyrex.logic.EventBus import InMemoryEventBus

            set_event_bus(InMemoryEventBus())


class TestEntityCacheAccessor:
    def test_set_and_get(self):
        from zephyrex.logic.AbstractLogicManager import (
            get_entity_cache,
            set_entity_cache,
        )

        original = get_entity_cache()
        try:
            sentinel = object()
            set_entity_cache(sentinel)
            assert get_entity_cache() is sentinel
        finally:
            set_entity_cache(original)

    def test_default_is_none(self):
        from zephyrex.logic.AbstractLogicManager import (
            get_entity_cache,
            set_entity_cache,
        )

        original = get_entity_cache()
        try:
            set_entity_cache(None)
            assert get_entity_cache() is None
        finally:
            set_entity_cache(original)


class TestResponseCacheAccessor:
    def test_set_and_get(self):
        from zephyrex.lib.ResponseCache import (
            get_response_cache,
            set_response_cache,
        )

        original = get_response_cache()
        try:
            sentinel = object()
            set_response_cache(sentinel)
            assert get_response_cache() is sentinel
        finally:
            set_response_cache(original)
