"""PRV_Valkey — concrete provider for the Valkey wire protocol.

Wraps the ``redis.asyncio`` client (the canonical Python implementation
of the Valkey/Redis wire protocol). Same client works against Valkey,
Redis, and any drop-in replacement that speaks the protocol.

Per Item 19, this provider is reachable as a *root* instance for
framework-internal use (EventBus streams transport, inbound rate
limiter, distributed counter Redis backend) and as *team*/*user*
instances for application-level per-tenant Valkey usage.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

from serverframework.extensions.valkey.EXT_Valkey import AbstractValkeyProvider
from serverframework.lib.Environment import env

_logger = logging.getLogger(__name__)


class PRV_Valkey(AbstractValkeyProvider):
    """Concrete Valkey/Redis-protocol provider.

    Connections are managed through a per-instance pool. The pool is
    instantiated lazily on first use; subsequent calls reuse the same
    pool. ``close()`` on the bonded instance disposes the pool.
    """

    name: str = "Valkey"
    friendly_name: str = "Valkey (Redis-protocol)"
    description: str = (
        "Valkey/Redis-protocol provider via redis-py asyncio client. "
        "Drop-in compatible with Valkey, Redis OSS (≤7.2), and Redis Inc.'s "
        "commercial distribution."
    )

    _connections: Dict[str, Any] = {}

    @classmethod
    def _resolve_url(cls, instance: Any) -> str:
        """Pick the Valkey URL from the instance's `api_key` (the
        canonical credentials slot per `ProviderInstanceModel`) or from
        the `VALKEY_URL` env var. Falls back to localhost so the
        development reference workflow works out-of-box."""
        url = (
            getattr(instance, "api_key", None)
            or env("VALKEY_URL")
            or "redis://localhost:6379/0"
        )
        return str(url)

    @classmethod
    def connect(cls, instance: Any) -> Any:
        """Return a connected async Valkey client for ``instance``.

        Lazy-imports `redis.asyncio` so the extension loads cleanly when
        the package is uninstalled (the dependency manager will install
        it before the extension is exercised at runtime).
        """
        url = cls._resolve_url(instance)
        if url in cls._connections:
            return cls._connections[url]
        try:
            from redis import asyncio as redis_asyncio  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "PRV_Valkey requires the `redis>=4.2` package on the "
                "Python path. The Valkey extension declares the "
                "dependency; install missing dependencies via the "
                "framework's dependency manager."
            ) from exc
        client = redis_asyncio.from_url(url, decode_responses=False)
        cls._connections[url] = client
        return client

    @classmethod
    async def close(cls, instance: Any) -> None:
        """Dispose the connection pool for ``instance``'s URL.

        Idempotent — repeated calls are no-ops after the first.
        """
        url = cls._resolve_url(instance)
        client = cls._connections.pop(url, None)
        if client is None:
            return
        try:
            await client.close()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("PRV_Valkey close failed for %s: %s", url, exc)

    @classmethod
    def build_streams_transport(
        cls, instance: Any, consumer_group: str = "serverframework"
    ) -> Any:
        """Return a `BrokerTransport` over Valkey Streams.

        The transport conforms to the EventBus's `BrokerTransport`
        contract (`send` / `subscribe` / `close`); the EventBus delegates
        every wire-level operation to it.

        Each topic maps to a stream key; a consumer group on the topic
        provides at-least-once delivery across consumer replicas.
        Producer side uses ``XADD``; consumer side uses ``XREADGROUP``
        in a loop with explicit ack on each handler success.
        """
        return _ValkeyStreamsTransport(
            provider=cls,
            instance=instance,
            consumer_group=consumer_group,
        )


class _ValkeyStreamsTransport:
    """Concrete `BrokerTransport` backed by `PRV_Valkey`.

    Implements the same contract as `serverframework.logic.EventBus.BrokerTransport`
    but lives in the extension to keep the redis-py dependency out of
    the framework's core. The EventBus consumes this transport
    structurally (duck-typed against the ABC).
    """

    def __init__(
        self,
        provider: type,
        instance: Any,
        consumer_group: str,
    ) -> None:
        self._provider = provider
        self._instance = instance
        self._group = consumer_group
        self._consumer_tasks: List[asyncio.Task] = []
        self._closed = False

    async def _client(self) -> Any:
        return self._provider.connect(self._instance)

    async def send(self, topic: str, payload: bytes) -> None:
        if self._closed:
            raise RuntimeError("Valkey streams transport is closed")
        client = await self._client()
        await client.xadd(topic, {"data": payload})

    async def subscribe(
        self, topic: str, handler: Callable[[bytes], Awaitable[None]]
    ) -> None:
        if self._closed:
            raise RuntimeError("Valkey streams transport is closed")
        client = await self._client()
        # Idempotent group create — `BUSYGROUP` is fine.
        try:
            await client.xgroup_create(topic, self._group, id="0", mkstream=True)
        except Exception:  # noqa: BLE001
            pass

        async def _consume() -> None:
            consumer_name = f"consumer-{id(handler)}"
            try:
                while not self._closed:
                    resp = await client.xreadgroup(
                        self._group,
                        consumer_name,
                        {topic: ">"},
                        count=10,
                        block=1000,
                    )
                    if not resp:
                        continue
                    for _stream, messages in resp:
                        for msg_id, fields in messages:
                            payload = (
                                fields.get(b"data")
                                if isinstance(fields, dict)
                                else None
                            )
                            if payload is None:
                                continue
                            try:
                                await handler(payload)
                            except Exception as exc:  # noqa: BLE001
                                _logger.error(
                                    "Valkey streams handler error on %s: %s",
                                    topic,
                                    exc,
                                )
                            try:
                                await client.xack(topic, self._group, msg_id)
                            except Exception:  # noqa: BLE001
                                pass
            except asyncio.CancelledError:
                raise

        self._consumer_tasks.append(asyncio.create_task(_consume()))

    async def close(self) -> None:
        self._closed = True
        for task in self._consumer_tasks:
            task.cancel()
        for task in self._consumer_tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._consumer_tasks.clear()
        await self._provider.close(self._instance)
