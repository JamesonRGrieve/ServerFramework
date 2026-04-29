"""Tests for ``lib.AdvisoryLock``."""

from __future__ import annotations

import asyncio

import pytest

from lib.AdvisoryLock import (
    InMemoryAdvisoryLock,
    LockTimeoutError,
    acquire_lock,
)


@pytest.fixture(autouse=True)
def _reset_locks():
    InMemoryAdvisoryLock._locks.clear()
    yield
    InMemoryAdvisoryLock._locks.clear()


@pytest.mark.unit
async def test_acquire_lock_serializes_critical_section():
    order: list[str] = []

    async def worker(name: str, sleep: float):
        async with acquire_lock("test.section"):
            order.append(f"start:{name}")
            await asyncio.sleep(sleep)
            order.append(f"end:{name}")

    await asyncio.gather(worker("a", 0.05), worker("b", 0.01))
    # The two critical sections must not interleave.
    assert order[0].startswith("start:")
    assert order[1].startswith("end:")
    assert order[2].startswith("start:")
    assert order[3].startswith("end:")


@pytest.mark.unit
async def test_acquire_lock_timeout_raises():
    async def hold():
        async with acquire_lock("test.held"):
            await asyncio.sleep(0.2)

    holder = asyncio.create_task(hold())
    await asyncio.sleep(0.01)  # ensure holder grabs first
    with pytest.raises(LockTimeoutError):
        async with acquire_lock("test.held", timeout=0.05):
            pass
    await holder


@pytest.mark.unit
async def test_distinct_names_do_not_block():
    started = asyncio.Event()
    finished = asyncio.Event()

    async def hold_a():
        async with acquire_lock("name.a"):
            started.set()
            await finished.wait()

    holder = asyncio.create_task(hold_a())
    await started.wait()
    # If 'name.b' is independent, this returns promptly.
    async with acquire_lock("name.b", timeout=0.5):
        pass
    finished.set()
    await holder


@pytest.mark.unit
async def test_lock_releases_on_exception():
    with pytest.raises(ValueError):
        async with acquire_lock("test.errpath"):
            raise ValueError("boom")
    # Should be reacquirable immediately.
    async with acquire_lock("test.errpath", timeout=0.1):
        pass
