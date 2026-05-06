"""PRV_Fake_DatabaseMemory — in-process fake for tests.

Mirrors the contract of every concrete provider in ``database_memory``
without requiring a backend SDK or a running server. The fake is
*protocol-agnostic*: it lives at the protocol-family level (under
``database_memory``, not under any one product) so it can stand in for
``PRV_Valkey``, a future ``PRV_Memcached``, ``PRV_DragonflyDB``, etc.
in tests that exercise a backend through the abstract contract.

The ``build_streams_transport`` method returns an in-memory transport
with the same publish/subscribe semantics as the real backed transport
(per-topic isolation, per-subscriber invocation, swallowed handler
errors logged but not raised).

Used by the EventBus test suite and by any other framework test that
needs a `BrokerTransport`-shaped object without a real broker.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List

from serverframework.extensions.database_memory.EXT_DatabaseMemory import (
    AbstractDatabaseMemoryProvider,
)

_logger = logging.getLogger(__name__)


class PRV_Fake_DatabaseMemory(AbstractDatabaseMemoryProvider):
    """In-process fake in-memory-store provider for tests.

    The "client" is a per-instance dict; `streams` data lives in another
    dict. Anything backend-shaped that the framework actually exercises
    (key-value set/get, streams send/subscribe) has a corresponding
    method on the in-memory client; anything else raises
    `NotImplementedError` so tests don't accidentally pass against a
    fake when they need real semantics.
    """

    name: str = "FakeDatabaseMemory"
    friendly_name: str = "In-process Fake (DatabaseMemory)"
    description: str = (
        "In-process fake; emits real publish/subscribe semantics for tests "
        "without standing up an in-memory store. Do not use in production."
    )

    _connections: Dict[str, "_FakeClient"] = {}

    @classmethod
    def connect(cls, instance: Any) -> "_FakeClient":
        key = id(instance)
        client = cls._connections.get(str(key))
        if client is None:
            client = _FakeClient()
            cls._connections[str(key)] = client
        return client

    @classmethod
    async def close(cls, instance: Any) -> None:
        cls._connections.pop(str(id(instance)), None)

    @classmethod
    def build_streams_transport(
        cls, instance: Any, consumer_group: str = "serverframework"
    ) -> Any:
        return _FakeDatabaseMemoryStreamsTransport(
            provider=cls,
            instance=instance,
            consumer_group=consumer_group,
        )


class _FakeClient:
    """Tiny in-process stand-in for an in-memory-store client."""

    def __init__(self) -> None:
        self.kv: Dict[bytes, bytes] = {}
        self.streams: Dict[str, List[bytes]] = defaultdict(list)
        self.subscribers: Dict[
            str, List[Callable[[bytes], Awaitable[None]]]
        ] = defaultdict(list)


class _FakeDatabaseMemoryStreamsTransport:
    """In-process `BrokerTransport`. Same shape as `_ValkeyStreamsTransport`
    in `PRV_Valkey.py`; per-topic isolation, swallowed handler errors."""

    def __init__(self, provider: type, instance: Any, consumer_group: str) -> None:
        self._provider = provider
        self._instance = instance
        self._group = consumer_group
        self._closed = False

    def _client(self) -> _FakeClient:
        return self._provider.connect(self._instance)

    async def send(self, topic: str, payload: bytes) -> None:
        if self._closed:
            raise RuntimeError("Fake DatabaseMemory streams transport is closed")
        client = self._client()
        client.streams[topic].append(payload)
        for handler in list(client.subscribers.get(topic, [])):
            try:
                await handler(payload)
            except Exception as exc:  # noqa: BLE001
                _logger.error(
                    "Fake DatabaseMemory handler error on %s: %s", topic, exc
                )

    async def subscribe(
        self, topic: str, handler: Callable[[bytes], Awaitable[None]]
    ) -> None:
        if self._closed:
            raise RuntimeError("Fake DatabaseMemory streams transport is closed")
        client = self._client()
        client.subscribers[topic].append(handler)

    async def close(self) -> None:
        self._closed = True
        await self._provider.close(self._instance)
