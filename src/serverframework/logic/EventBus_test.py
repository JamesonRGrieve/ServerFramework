"""Tests for the cross-process event bus seam (Item 42)."""

import pytest
from pydantic import BaseModel

from serverframework.logic.EventBus import (
    AbstractEventBus,
    InMemoryEventBus,
    KafkaEventBus,
    NATSEventBus,
    RedisStreamsEventBus,
    get_event_bus,
    on_event,
    set_event_bus,
)


class UserCreated(BaseModel):
    user_id: str
    email: str


class OrderCreated(BaseModel):
    order_id: str
    total: float


@pytest.mark.asyncio
async def test_publish_runs_subscribed_handlers_in_order():
    bus = InMemoryEventBus()
    calls: list = []

    bus.subscribe(UserCreated, lambda e: calls.append(("first", e.user_id)))
    bus.subscribe(UserCreated, lambda e: calls.append(("second", e.user_id)))

    await bus.publish(UserCreated(user_id="u1", email="a@b.com"))

    assert calls == [("first", "u1"), ("second", "u1")]


@pytest.mark.asyncio
async def test_in_memory_handler_exception_propagates_to_publisher():
    """InMemoryEventBus surfaces handler errors; in-process callers own
    the contract. Real broker adapters MUST swallow + DLQ instead."""
    bus = InMemoryEventBus()

    def boom(event):
        raise ValueError("handler exploded")

    bus.subscribe(UserCreated, boom)

    with pytest.raises(ValueError, match="handler exploded"):
        await bus.publish(UserCreated(user_id="u1", email="a@b.com"))


@pytest.mark.asyncio
async def test_subscribe_keys_by_event_class():
    bus = InMemoryEventBus()
    user_calls: list = []
    order_calls: list = []

    bus.subscribe(UserCreated, lambda e: user_calls.append(e))
    bus.subscribe(OrderCreated, lambda e: order_calls.append(e))

    await bus.publish(UserCreated(user_id="u1", email="a@b.com"))

    assert len(user_calls) == 1
    assert len(order_calls) == 0

    await bus.publish(OrderCreated(order_id="o1", total=12.5))

    assert len(user_calls) == 1
    assert len(order_calls) == 1


@pytest.mark.asyncio
async def test_close_clears_subscribers_and_refuses_publish():
    bus = InMemoryEventBus()
    calls: list = []
    bus.subscribe(UserCreated, lambda e: calls.append(e))

    await bus.close()

    assert bus._subscribers == {}
    with pytest.raises(RuntimeError, match="closed"):
        await bus.publish(UserCreated(user_id="u1", email="a@b.com"))


@pytest.mark.asyncio
async def test_on_event_decorator_registers_on_active_bus():
    bus = InMemoryEventBus()
    previous = get_event_bus()
    set_event_bus(bus)
    try:
        calls: list = []

        @on_event(UserCreated)
        def handle(event: UserCreated):
            calls.append(event.user_id)

        await bus.publish(UserCreated(user_id="u42", email="x@y.com"))

        assert calls == ["u42"]
    finally:
        set_event_bus(previous)


@pytest.mark.asyncio
async def test_set_and_get_event_bus_round_trip():
    previous = get_event_bus()
    new_bus = InMemoryEventBus()
    set_event_bus(new_bus)
    try:
        assert get_event_bus() is new_bus
    finally:
        set_event_bus(previous)
    assert get_event_bus() is previous


@pytest.mark.asyncio
async def test_async_handler_runs_to_completion():
    bus = InMemoryEventBus()
    calls: list = []

    async def handle(event: UserCreated):
        calls.append(("start", event.user_id))
        calls.append(("end", event.user_id))

    bus.subscribe(UserCreated, handle)

    await bus.publish(UserCreated(user_id="u9", email="z@z.com"))

    assert calls == [("start", "u9"), ("end", "u9")]


def test_broker_buses_are_abstract_event_bus_subclasses():
    """Item 42 follow-up: KafkaEventBus, NATSEventBus, RedisStreamsEventBus
    are real adapters now, not stubs. Each subclass must conform to
    `AbstractEventBus` and accept a transport for test injection."""
    assert issubclass(KafkaEventBus, AbstractEventBus)
    assert issubclass(NATSEventBus, AbstractEventBus)
    assert issubclass(RedisStreamsEventBus, AbstractEventBus)


def test_kafka_event_bus_constructor_accepts_transport():
    from serverframework.logic.EventBus import InMemoryBrokerTransport

    bus = KafkaEventBus(transport=InMemoryBrokerTransport())
    assert bus._transport is not None


def test_nats_event_bus_constructor_accepts_transport():
    from serverframework.logic.EventBus import InMemoryBrokerTransport

    bus = NATSEventBus(transport=InMemoryBrokerTransport())
    assert bus._transport is not None


def test_redis_streams_event_bus_constructor_accepts_transport():
    from serverframework.logic.EventBus import InMemoryBrokerTransport

    bus = RedisStreamsEventBus(transport=InMemoryBrokerTransport())
    assert bus._transport is not None


def test_no_sdk_factory_methods_on_broker_buses():
    """The framework's core does not import broker SDKs. Each adapter
    accepts a `BrokerTransport` at construction time; the production
    transport comes from an extension (Valkey for Redis-protocol, Kafka
    extension when one ships, etc.). Verify there's no `from_url`,
    `from_bootstrap`, or `from_servers` factory leaking SDK imports
    into the framework's core."""
    assert not hasattr(RedisStreamsEventBus, "from_url")
    assert not hasattr(KafkaEventBus, "from_bootstrap")
    assert not hasattr(NATSEventBus, "from_servers")


# ---------------------------------------------------------------------------
# Real broker semantics via in-memory transport
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kafka_event_bus_publishes_through_transport():
    """End-to-end: subscribe then publish via the in-memory transport.
    Verifies the adapter's serialize → topic → deserialize → dispatch
    path without standing up a real Kafka broker."""
    from serverframework.logic.EventBus import InMemoryBrokerTransport

    transport = InMemoryBrokerTransport()
    bus = KafkaEventBus(transport=transport)

    received: list = []

    async def _handler(event: UserCreated):
        received.append(event.user_id)

    bus.subscribe(UserCreated, _handler)
    # The subscribe-time wiring schedules a transport.subscribe call.
    # Give the loop a tick so the topic registers.
    import asyncio as _asyncio

    await _asyncio.sleep(0)
    await bus.publish(UserCreated(user_id="u-77", email="x@x"))

    assert received == ["u-77"]
    await bus.close()


@pytest.mark.asyncio
async def test_broker_swallows_handler_errors_unlike_inmemory():
    """Real broker adapters MUST swallow handler errors (cross-process
    fan-out cannot let one bad subscriber break the publisher). The
    in-memory adapter raises; the broker adapters log + continue."""
    from serverframework.logic.EventBus import InMemoryBrokerTransport

    transport = InMemoryBrokerTransport()
    bus = KafkaEventBus(transport=transport)

    async def _bad_handler(event: UserCreated):
        raise ValueError("whoops")

    bus.subscribe(UserCreated, _bad_handler)
    import asyncio as _asyncio

    await _asyncio.sleep(0)
    # Publishing must not raise even though the handler does.
    await bus.publish(UserCreated(user_id="u-1", email="x@x"))
    await bus.close()


@pytest.mark.asyncio
async def test_broker_dlq_receives_failed_payload_when_configured():
    """A subscriber failure with `dlq_topic` set forwards the payload
    to the DLQ topic. Verified by subscribing a probe handler to the DLQ."""
    from serverframework.logic.EventBus import InMemoryBrokerTransport

    transport = InMemoryBrokerTransport()
    dlq_received: list = []

    async def _dlq_capture(payload: bytes) -> None:
        dlq_received.append(payload)

    await transport.subscribe("dlq-failed", _dlq_capture)

    bus = KafkaEventBus(transport=transport, dlq_topic="dlq-failed")

    async def _bad_handler(event: UserCreated):
        raise RuntimeError("boom")

    bus.subscribe(UserCreated, _bad_handler)
    import asyncio as _asyncio

    await _asyncio.sleep(0)
    await bus.publish(UserCreated(user_id="u-9", email="x@x"))

    assert len(dlq_received) == 1
    payload = dlq_received[0]
    decoded = UserCreated.model_validate_json(payload)
    assert decoded.user_id == "u-9"
    await bus.close()


@pytest.mark.asyncio
async def test_broker_close_drains_transport():
    from serverframework.logic.EventBus import InMemoryBrokerTransport

    transport = InMemoryBrokerTransport()
    bus = KafkaEventBus(transport=transport)
    await bus.close()
    assert transport._closed is True


@pytest.mark.asyncio
async def test_in_memory_broker_transport_round_trip():
    from serverframework.logic.EventBus import InMemoryBrokerTransport

    transport = InMemoryBrokerTransport()
    received: list = []

    async def _handler(payload: bytes) -> None:
        received.append(payload)

    await transport.subscribe("topic-x", _handler)
    await transport.send("topic-x", b"hello")
    assert received == [b"hello"]


@pytest.mark.asyncio
async def test_in_memory_broker_transport_isolates_topics():
    from serverframework.logic.EventBus import InMemoryBrokerTransport

    transport = InMemoryBrokerTransport()
    a_received: list = []
    b_received: list = []

    async def _a(payload: bytes) -> None:
        a_received.append(payload)

    async def _b(payload: bytes) -> None:
        b_received.append(payload)

    await transport.subscribe("topic-a", _a)
    await transport.subscribe("topic-b", _b)
    await transport.send("topic-a", b"a-msg")

    assert a_received == [b"a-msg"]
    assert b_received == []


@pytest.mark.asyncio
async def test_published_events_property_records_publishes():
    bus = InMemoryEventBus()
    e1 = UserCreated(user_id="u1", email="a@b.com")
    e2 = OrderCreated(order_id="o1", total=1.0)
    await bus.publish(e1)
    await bus.publish(e2)

    assert bus.published_events == [e1, e2]
