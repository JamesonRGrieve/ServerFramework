"""Tests for ``lib.DistributedCounter``."""

from __future__ import annotations

import asyncio

import pytest

from serverframework.lib.DistributedCounter import (
    CounterExhaustedError,
    DistributedCounter,
    InMemoryDistributedCounter,
    PostgresDistributedCounter,
    default_counter,
)


@pytest.fixture(autouse=True)
def _reset_state():
    InMemoryDistributedCounter._reset_all()
    yield
    InMemoryDistributedCounter._reset_all()


@pytest.mark.unit
async def test_try_consume_succeeds_under_limit():
    counter = InMemoryDistributedCounter("test:rps", limit=5)
    for _ in range(5):
        assert await counter.try_consume() is True
    assert await counter.consumed() == 5


@pytest.mark.unit
async def test_try_consume_refuses_to_exceed_limit():
    counter = InMemoryDistributedCounter("test:rps", limit=3)
    assert await counter.try_consume(2) is True
    assert await counter.try_consume(2) is False  # would exceed (2+2 > 3)
    assert await counter.consumed() == 2


@pytest.mark.unit
async def test_release_credits_back():
    counter = InMemoryDistributedCounter("test:rps", limit=5)
    assert await counter.try_consume(5) is True
    assert await counter.try_consume(1) is False
    await counter.release(2)
    assert await counter.consumed() == 3
    assert await counter.try_consume(2) is True


@pytest.mark.unit
async def test_reset_rolls_to_new_period():
    counter = InMemoryDistributedCounter("test:rps", limit=5, period_key="2026-01")
    await counter.try_consume(5)
    assert await counter.consumed() == 5
    await counter.reset(period_key="2026-02")
    assert await counter.consumed() == 0
    assert counter.period_key == "2026-02"


@pytest.mark.unit
async def test_concurrent_consumers_respect_limit():
    counter = InMemoryDistributedCounter("test:race", limit=10)

    async def attempt():
        return await counter.try_consume(1)

    results = await asyncio.gather(*[attempt() for _ in range(50)])
    assert sum(1 for r in results if r) == 10
    assert await counter.consumed() == 10


@pytest.mark.unit
def test_postgres_counter_requires_session_provider():
    counter = PostgresDistributedCounter("k", limit=10)
    with pytest.raises(RuntimeError):
        asyncio.get_event_loop().run_until_complete(counter.try_consume(1))


@pytest.mark.unit
def test_abstract_subclasses_register():
    assert issubclass(InMemoryDistributedCounter, DistributedCounter)
    assert issubclass(PostgresDistributedCounter, DistributedCounter)
    assert issubclass(CounterExhaustedError, Exception)


# ---------------------------------------------------------------------------
# No-arg default and Quota-list signature (Item 69 acceptance criteria)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_distributed_counter_no_arg_constructible():
    """Quota.try_consume integration depends on this no-arg path."""
    counter = DistributedCounter()
    assert counter is not None


@pytest.mark.unit
def test_default_counter_factory_returns_distributed_counter():
    counter = default_counter()
    assert isinstance(counter, DistributedCounter)


@pytest.mark.unit
def test_no_arg_counter_supports_quota_list_call_shape():
    """Verifies the polymorphic try_consume(quotas, amount=...) shape.

    Note: the list-shape is intentionally sync (returns bool, not coroutine)
    so callers like Quota.try_consume can use it without awaiting.
    """
    class _Row:
        def __init__(self, limit: int, consumed: int = 0) -> None:
            self.limit = limit
            self.consumed = consumed

        def is_exhausted(self, additional: int = 0) -> bool:
            return (self.consumed + additional) > self.limit

    a = _Row(limit=10, consumed=0)
    b = _Row(limit=20, consumed=5)
    counter = DistributedCounter()
    assert counter.try_consume([a, b], amount=3) is True
    assert a.consumed == 3
    assert b.consumed == 8


@pytest.mark.unit
def test_quota_list_refuses_if_any_row_exhausted():
    class _Row:
        def __init__(self, limit: int, consumed: int = 0) -> None:
            self.limit = limit
            self.consumed = consumed

        def is_exhausted(self, additional: int = 0) -> bool:
            return (self.consumed + additional) > self.limit

    a = _Row(limit=10, consumed=8)
    b = _Row(limit=5, consumed=4)  # only 1 left
    counter = DistributedCounter()
    assert counter.try_consume([a, b], amount=3) is False
    assert a.consumed == 8  # unchanged
    assert b.consumed == 4
