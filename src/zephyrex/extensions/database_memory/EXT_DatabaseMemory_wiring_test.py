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


@pytest.fixture(autouse=True)
def _restore_framework_backend_globals():
    """``wire_framework_backends()`` sets five process-global backends at once,
    but each test here patches only the one setter it asserts on — so the others
    leak a Mock-backed backend (e.g. a ``ValkeyEntityCache`` wrapping an
    ``AsyncMock``) into module globals and contaminate later tests sharing the
    xdist worker. A leaked entity cache is especially harmful: session
    enforcement (``_enforce_session_not_revoked``) then sees a truthy
    ``get_by_field`` hit and short-circuits, silently disabling enforcement in
    unrelated tests. Snapshot and restore every global these tests can touch — by
    raw module attribute, so no getter lazy-init or setter side effect fires."""
    import zephyrex.lib.InboundSecurity as _inbound
    import zephyrex.lib.ReplayCache as _replay
    import zephyrex.lib.ResponseCache as _response
    import zephyrex.logic.AbstractLogicManager as _alm
    import zephyrex.logic.EventBus as _eventbus

    saved = (
        (_alm, "_entity_cache", _alm._entity_cache),
        (_eventbus, "_active_bus", _eventbus._active_bus),
        (_replay, "_GLOBAL_REPLAY_CACHE", _replay._GLOBAL_REPLAY_CACHE),
        (_response, "_response_cache", _response._response_cache),
        (_inbound, "_rate_limit_counter", _inbound._rate_limit_counter),
    )
    try:
        yield
    finally:
        for module, name, value in saved:
            setattr(module, name, value)


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
