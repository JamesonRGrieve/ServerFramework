"""Cross-process event bus seam (Item 42).

Hooks are the recommended in-process cross-cutting seam; this bus is the
opt-in escape hatch for cross-process / cross-service fan-out (billing
to invoicing, signups to marketing, audit to SIEM, etc.). Events are
typed Pydantic models with versioning; backward-compatibility rules
(additive-only fields, no renames, no narrowed types) live in
EXT.Patterns.md once Item 52 lands.

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

Schema-registry integration is out of scope for v1. Backward-compatibility
rules (additive-only fields, no renames, no narrowed types) should be
enforced by a CI compatibility checker modeled on Item 11's drift
snapshots; that enforcement is a follow-up item.

This module ships:
    - AbstractEventBus: typed publish/subscribe contract
    - InMemoryEventBus: in-process default for tests + single-process
    - KafkaEventBus / NATSEventBus / RedisStreamsEventBus: ABC stubs
      with documented constructor shape; real broker integration is
      deferred to follow-up items
    - on_event decorator: subscribe a handler

The bus is fed by the outbox (Item 35) when transactional publish-
and-local-mutation atomicity is required. See Item 28's
QueueConsumerService for the runtime that drains subscribed handlers.

Error semantics
---------------
``InMemoryEventBus`` propagates handler exceptions to the publisher: the
in-memory adapter exists for in-process semantics where the caller owns
the contract, so swallowing would hide real bugs in tests. Real broker
adapters (Kafka/NATS/Redis) MUST swallow per-handler errors and route
the failing event to a dead-letter queue; cross-process fan-out cannot
let one bad subscriber break the publisher.
"""

from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Awaitable, Callable, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel

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


class KafkaEventBus(AbstractEventBus):
    """ABC stub. Real Kafka integration ships in a follow-up item where
    the SDK can be tested against a real broker.

    Constructor shape: KafkaEventBus(bootstrap_servers, client_id, ...)
    """

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "KafkaEventBus is a contract stub; real integration deferred. "
            "Use InMemoryEventBus for testing or wire your own Kafka adapter "
            "against this ABC."
        )

    async def publish(self, event: BaseModel) -> None:  # pragma: no cover
        raise NotImplementedError

    def subscribe(self, event_class: Type[BaseModel], handler: EventHandler) -> None:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover
        raise NotImplementedError


class NATSEventBus(AbstractEventBus):
    """ABC stub. Real NATS integration deferred (same shape as Kafka)."""

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "NATSEventBus is a contract stub; real integration deferred."
        )

    async def publish(self, event: BaseModel) -> None:  # pragma: no cover
        raise NotImplementedError

    def subscribe(self, event_class: Type[BaseModel], handler: EventHandler) -> None:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover
        raise NotImplementedError


class RedisStreamsEventBus(AbstractEventBus):
    """ABC stub. Real Redis Streams integration deferred."""

    def __init__(self, *args, **kwargs) -> None:
        raise NotImplementedError(
            "RedisStreamsEventBus is a contract stub; real integration deferred."
        )

    async def publish(self, event: BaseModel) -> None:  # pragma: no cover
        raise NotImplementedError

    def subscribe(self, event_class: Type[BaseModel], handler: EventHandler) -> None:  # pragma: no cover
        raise NotImplementedError

    async def close(self) -> None:  # pragma: no cover
        raise NotImplementedError


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
    "KafkaEventBus",
    "NATSEventBus",
    "RedisStreamsEventBus",
    "EventHandler",
    "set_event_bus",
    "get_event_bus",
    "on_event",
]
