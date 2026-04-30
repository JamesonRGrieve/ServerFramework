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


def test_kafka_event_bus_constructor_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="contract stub"):
        KafkaEventBus("localhost:9092")


def test_nats_event_bus_constructor_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="contract stub"):
        NATSEventBus("nats://localhost:4222")


def test_redis_streams_event_bus_constructor_raises_not_implemented():
    with pytest.raises(NotImplementedError, match="contract stub"):
        RedisStreamsEventBus("redis://localhost:6379")


def test_stub_buses_are_abstract_event_bus_subclasses():
    assert issubclass(KafkaEventBus, AbstractEventBus)
    assert issubclass(NATSEventBus, AbstractEventBus)
    assert issubclass(RedisStreamsEventBus, AbstractEventBus)


@pytest.mark.asyncio
async def test_published_events_property_records_publishes():
    bus = InMemoryEventBus()
    e1 = UserCreated(user_id="u1", email="a@b.com")
    e2 = OrderCreated(order_id="o1", total=1.0)
    await bus.publish(e1)
    await bus.publish(e2)

    assert bus.published_events == [e1, e2]
