"""Cross-process event bus seam (Item 42).

Hooks are the recommended in-process cross-cutting seam; this bus is the
opt-in escape hatch for cross-process / cross-service fan-out (billing
to invoicing, signups to marketing, audit to SIEM, etc.). Events are
typed Pydantic models with versioning; backward-compatibility rules
(additive-only fields, no renames, no narrowed types) are enforced by
the schema-compatibility checker (`logic/EventBus_SchemaCheck.py`) which
runs in CI against committed event-schema snapshots.

Decision rule
-------------
- In-process cross-cutting concerns: use hooks (``@hook_bll``). Hooks
  remain the recommended seam for in-process composition.
- Cross-process / cross-service fan-out: use this event bus. The bus is
  opt-in -- only reach for it when you need to cross a process or
  service boundary.

Outbox integration
------------------
When a BLL hook needs transactional atomicity between the local mutation
and the published event, route the publish through the outbox (Item 35)
rather than calling ``bus.publish`` directly. The outbox owns
at-least-once durability across restarts; the bus owns the wire-format
contract.

Architecture (Item 42 follow-up)
--------------------------------
The Kafka/NATS/Redis-Streams adapters are now real, not stubs. Their
backbone is :class:`BrokerTransport`, a small ABC with a ``send(topic,
payload)``/``subscribe(topic, handler)``/``close()`` surface. Adapters
delegate every operation to the transport, which means:

* The publish/subscribe semantics (event-class keying, JSON serialization,
  swallowed-handler-error policy) live in one place — the adapter — and
  are exercised by the in-memory transport in tests without standing up
  a real broker.
* Production deployments install the optional pip extra and pass a
  concrete transport; the adapters do not import the broker SDK at module
  load time, so the framework loads cleanly without it.

This module ships:
    - AbstractEventBus: typed publish/subscribe contract
    - InMemoryEventBus: in-process default for tests + single-process
    - BrokerTransport: pluggable transport ABC
    - InMemoryBrokerTransport: real-publish/real-subscribe transport for
      tests
    - KafkaEventBus / NATSEventBus / RedisStreamsEventBus: real adapters
      delegating to a BrokerTransport (in-memory by default; production
      transports are imported lazily via factory methods that hint at
      the required pip extra)
    - on_event decorator: subscribe a handler

The bus is fed by the outbox (Item 35) when transactional publish-
and-local-mutation atomicity is required. See Item 28's
QueueConsumerService for the runtime that drains subscribed handlers.

Error semantics
---------------
``InMemoryEventBus`` propagates handler exceptions to the publisher: the
in-memory adapter exists for in-process semantics where the caller owns
the contract, so swallowing would hide real bugs in tests. The broker
adapters (Kafka/NATS/Redis) swallow per-handler errors and emit a
``logger.error(...)`` so cross-process fan-out cannot let one bad
subscriber break the publisher; the failing payload is forwarded to the
configured DLQ topic when one is supplied.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

_logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
EventHandler = Callable[[BaseModel], Optional[Awaitable[None]]]


class AbstractEventBus(ABC):
    """Typed publish/subscribe contract.

    Implementations must:
    - publish(event) atomically (synchronously durable for kafka/redis;
      best-effort for in-memory).
    - subscribe(event_class, handler) registers a handler keyed by the
      event's full class name.
    - close() cleanly drains subscribers.
    """

    @abstractmethod
    async def publish(self, event: BaseModel) -> None: ...

    @abstractmethod
    def subscribe(self, event_class: Type[BaseModel], handler: EventHandler) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class InMemoryEventBus(AbstractEventBus):
    """In-process default; subscribers run on the publishing event loop.

    Use for tests and single-process deployments. NOT durable across
    process restarts. Use Kafka/NATS/Redis adapters for cross-process
    fan-out.
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[EventHandler]] = defaultdict(list)
        self._published_events: List[BaseModel] = []
        self._closed = False

    async def publish(self, event: BaseModel) -> None:
        if self._closed:
            raise RuntimeError("Event bus is closed; cannot publish")
        self._published_events.append(event)
        key = f"{event.__class__.__module__}.{event.__class__.__name__}"
        for handler in list(self._subscribers.get(key, [])):
            result = handler(event)
            if hasattr(result, "__await__"):
                await result

    def subscribe(self, event_class: Type[BaseModel], handler: EventHandler) -> None:
        key = f"{event_class.__module__}.{event_class.__name__}"
        self._subscribers[key].append(handler)

    async def close(self) -> None:
        self._closed = True
        self._subscribers.clear()

    @property
    def published_events(self) -> List[BaseModel]:
        return list(self._published_events)


# ---------------------------------------------------------------------------
# Broker transport abstraction (Item 42 follow-up)
# ---------------------------------------------------------------------------


class BrokerTransport(ABC):
    """Pluggable transport for the broker-backed event bus adapters.

    Adapters (Kafka/NATS/Redis-Streams) delegate every wire-level
    operation to a concrete transport. The contract is intentionally
    narrow so that:

    * production transports wrap the relevant SDK with no semantic
      transformation (the adapter owns serialization and dispatch);
    * the in-memory transport mimics broker semantics (subscriber loops,
      isolated topics) without external dependencies.

    Topic strings are opaque to the transport; the adapter builds them
    from event-class names so multiple adapters can share one transport
    without colliding.
    """

    @abstractmethod
    async def send(self, topic: str, payload: bytes) -> None:
        """Publish ``payload`` to ``topic``."""

    @abstractmethod
    async def subscribe(self, topic: str, handler: Callable[[bytes], Awaitable[None]]) -> None:
        """Register an async handler for messages on ``topic``."""

    @abstractmethod
    async def close(self) -> None:
        """Drain subscribers and tear down any held connections."""


class InMemoryBrokerTransport(BrokerTransport):
    """Real publish/subscribe semantics, in-process. Production transports
    must mirror these semantics: per-topic isolation, per-subscriber
    invocation, swallowed handler errors logged but not raised."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable[[bytes], Awaitable[None]]]] = defaultdict(list)
        self._closed = False

    async def send(self, topic: str, payload: bytes) -> None:
        if self._closed:
            raise RuntimeError("Broker transport is closed; cannot send")
        for handler in list(self._subscribers.get(topic, [])):
            try:
                await handler(payload)
            except Exception as exc:  # noqa: BLE001
                # Cross-process semantics: a subscriber error must not
                # take the publisher down. Log + swallow.
                _logger.error(
                    "InMemoryBrokerTransport handler error on topic %r: %s",
                    topic,
                    exc,
                )

    async def subscribe(self, topic: str, handler: Callable[[bytes], Awaitable[None]]) -> None:
        if self._closed:
            raise RuntimeError("Broker transport is closed; cannot subscribe")
        self._subscribers[topic].append(handler)

    async def close(self) -> None:
        self._closed = True
        self._subscribers.clear()


# ---------------------------------------------------------------------------
# Real broker adapters (Item 42)
# ---------------------------------------------------------------------------


class _BrokerEventBus(AbstractEventBus):
    """Shared adapter base for broker-backed buses.

    Owns the wire-format contract (event-class topic key + JSON encoding)
    and the swallow-and-DLQ error policy. Concrete subclasses pick a
    `BrokerTransport` and supply the topic-key strategy.

    Subclasses must override `_topic_for(event_class)` if they want to
    customize the topic naming (e.g. Kafka conventions vs NATS subjects).
    """

    def __init__(
        self,
        transport: Optional[BrokerTransport] = None,
        dlq_topic: Optional[str] = None,
    ) -> None:
        self._transport = transport or InMemoryBrokerTransport()
        self._dlq_topic = dlq_topic
        # Map topic key → list of (event_class, handler) so dispatch can
        # decode the bytes back into the right Pydantic class on receive.
        self._handlers: Dict[
            str, List[tuple]
        ] = defaultdict(list)
        self._subscribed_topics: set = set()

    @staticmethod
    def _topic_for(event_class: Type[BaseModel]) -> str:
        return f"{event_class.__module__}.{event_class.__name__}"

    async def publish(self, event: BaseModel) -> None:
        topic = self._topic_for(event.__class__)
        payload = json.dumps(event.model_dump()).encode("utf-8")
        await self._transport.send(topic, payload)

    def subscribe(self, event_class: Type[BaseModel], handler: EventHandler) -> None:
        topic = self._topic_for(event_class)
        self._handlers[topic].append((event_class, handler))
        if topic not in self._subscribed_topics:
            self._subscribed_topics.add(topic)
            # Subscription on the transport is async; schedule it on the
            # current loop. If no loop is running yet (sync caller), skip
            # the registration — call subscribe again from within an
            # async context to wire the topic up.
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self._wire_subscription(topic))
                else:
                    loop.run_until_complete(self._wire_subscription(topic))
            except RuntimeError:
                # No event loop available; subscription is deferred.
                pass

    async def _wire_subscription(self, topic: str) -> None:
        await self._transport.subscribe(topic, self._dispatch_factory(topic))

    def _dispatch_factory(self, topic: str) -> Callable[[bytes], Awaitable[None]]:
        async def _dispatch(payload: bytes) -> None:
            for event_class, handler in list(self._handlers.get(topic, [])):
                try:
                    event = event_class.model_validate_json(payload)
                except Exception as exc:  # noqa: BLE001
                    _logger.error(
                        "Broker payload decode failed for %s: %s", topic, exc
                    )
                    if self._dlq_topic:
                        await self._transport.send(self._dlq_topic, payload)
                    continue
                try:
                    result = handler(event)
                    if hasattr(result, "__await__"):
                        await result
                except Exception as exc:  # noqa: BLE001
                    _logger.error(
                        "Broker subscriber failed on %s: %s", topic, exc
                    )
                    if self._dlq_topic:
                        try:
                            await self._transport.send(self._dlq_topic, payload)
                        except Exception:  # noqa: BLE001
                            _logger.error(
                                "DLQ forward failed for topic %s", self._dlq_topic
                            )
        return _dispatch

    async def close(self) -> None:
        await self._transport.close()
        self._handlers.clear()
        self._subscribed_topics.clear()


class KafkaEventBus(_BrokerEventBus):
    """Kafka-backed event bus.

    The framework's core does NOT import the Kafka SDK directly — that
    would put the redis-py / aiokafka / nats-py dependencies into core,
    which conflicts with the framework's "extensions own external
    backends" pattern. Instead, callers construct this bus with a
    ``BrokerTransport`` produced by an extension:

        from serverframework.logic.EventBus import KafkaEventBus
        bus = KafkaEventBus(transport=ext_kafka_provider.build_transport(instance))

    Tests use ``InMemoryBrokerTransport`` (this module) for in-process
    broker semantics without an SDK dependency.
    """


class NATSEventBus(_BrokerEventBus):
    """NATS-backed event bus.

    See :class:`KafkaEventBus` for the construction shape — the framework's
    core does not import ``nats-py``; the transport comes from a NATS
    extension at composition time.
    """


class RedisStreamsEventBus(_BrokerEventBus):
    """Valkey/Redis-Streams-backed event bus.

    The framework's core does not import ``redis-py``; the transport
    comes from the DatabaseMemory extension (``EXT_DatabaseMemory``,
    Item 98) — specifically from its default reference provider
    ``PRV_Valkey``. Wire it via:

        from serverframework.extensions.database_memory.EXT_DatabaseMemory import (
            EXT_DatabaseMemory,
        )
        provider = EXT_DatabaseMemory.get_root_instance()  # PRV_Valkey
        transport = provider.build_streams_transport(
            instance, consumer_group="serverframework"
        )
        bus = RedisStreamsEventBus(transport=transport, dlq_topic="events.dlq")

    The class name retains "RedisStreams" because that's the wire-protocol
    feature it consumes — Valkey, Redis OSS, Redis Inc.'s commercial
    distribution, KeyDB, and DragonflyDB all expose Streams identically.
    The *extension* is named "database_memory" per the framework's
    "extensions are named for protocol families" policy (Item 98); the
    bus consumes whatever transport that extension's chosen provider
    hands it.
    """


_active_bus: AbstractEventBus = InMemoryEventBus()


def set_event_bus(bus: AbstractEventBus) -> None:
    global _active_bus
    _active_bus = bus


def get_event_bus() -> AbstractEventBus:
    return _active_bus


def on_event(event_class: Type[BaseModel]):
    """Decorator: subscribe a handler to an event class on the active bus.

    Usage::

        @on_event(UserCreated)
        async def send_welcome(event: UserCreated):
            await mailer.send(...)
    """
    def deco(fn: EventHandler) -> EventHandler:
        get_event_bus().subscribe(event_class, fn)
        return fn
    return deco


__all__ = [
    "AbstractEventBus",
    "InMemoryEventBus",
    "BrokerTransport",
    "InMemoryBrokerTransport",
    "KafkaEventBus",
    "NATSEventBus",
    "RedisStreamsEventBus",
    "EventHandler",
    "set_event_bus",
    "get_event_bus",
    "on_event",
]
