"""Item 85 — correlation_id contextvar tests.

Covers the public surface added for the structured-logging contract:
``set_correlation_id`` / ``get_correlation_id`` / ``clear_correlation_id``,
the ``clear_request_context`` integration, and the ``wrap_in_context``
helper that propagates the id across ``asyncio.to_thread`` and
``asyncio.create_task`` boundaries.

No mocks (per AGENTS.md no-mock pillar): the tests exercise real
contextvars, real ``asyncio`` primitives, and real worker threads.
"""

from __future__ import annotations

import asyncio
import contextvars
import threading

import pytest

from zephyrex.lib.RequestContext import (
    clear_correlation_id,
    clear_request_context,
    get_correlation_id,
    mint_correlation_id,
    set_correlation_id,
    set_request_user,
    wrap_in_context,
)


@pytest.fixture(autouse=True)
def _reset_context():
    clear_request_context()
    yield
    clear_request_context()


# ----- Basic getter/setter --------------------------------------------------


@pytest.mark.unit
def test_correlation_id_default_is_none():
    assert get_correlation_id() is None


@pytest.mark.unit
def test_correlation_id_roundtrip():
    set_correlation_id("abc123")
    assert get_correlation_id() == "abc123"


@pytest.mark.unit
def test_clear_correlation_id_resets_to_none():
    set_correlation_id("abc123")
    clear_correlation_id()
    assert get_correlation_id() is None


@pytest.mark.unit
def test_clear_request_context_clears_correlation_id():
    """``clear_request_context`` is the one helper request middleware
    calls; it must purge correlation id alongside user info and deadline.
    """
    set_correlation_id("req-1")
    set_request_user({"user_id": "u1"})
    clear_request_context()
    assert get_correlation_id() is None


@pytest.mark.unit
def test_mint_correlation_id_returns_unique_hex():
    a = mint_correlation_id()
    b = mint_correlation_id()
    assert a != b
    assert len(a) == 32
    int(a, 16)  # must be hex


# ----- wrap_in_context across asyncio.to_thread -----------------------------


@pytest.mark.unit
def test_wrap_in_context_propagates_to_thread():
    """A sync callable wrapped via ``wrap_in_context`` and dispatched
    through ``asyncio.to_thread`` reads the correlation id set in the
    calling task — even though the worker thread has its own context."""
    set_correlation_id("propagate-me")
    captured: dict = {}

    def _sync_worker():
        captured["cid"] = get_correlation_id()
        captured["thread"] = threading.current_thread().name

    async def _driver():
        wrapped = wrap_in_context(_sync_worker)
        await asyncio.to_thread(wrapped)

    asyncio.run(_driver())

    assert captured["cid"] == "propagate-me"
    # to_thread always dispatches off the main thread.
    assert captured["thread"] != threading.main_thread().name


@pytest.mark.unit
def test_wrap_in_context_to_thread_carries_args_and_kwargs():
    set_correlation_id("with-args")
    captured: dict = {}

    def _worker(a, b, *, c):
        captured["sum"] = a + b + c
        captured["cid"] = get_correlation_id()

    async def _driver():
        wrapped = wrap_in_context(_worker, 1, 2, c=3)
        await asyncio.to_thread(wrapped)

    asyncio.run(_driver())

    assert captured["sum"] == 6
    assert captured["cid"] == "with-args"


@pytest.mark.unit
def test_wrap_in_context_snapshot_isolated_from_post_call_mutation():
    """The snapshot is taken at ``wrap_in_context`` call time. Mutating
    the contextvar after wrapping must NOT change what the worker sees."""
    set_correlation_id("snap-1")

    def _worker():
        return get_correlation_id()

    wrapped = wrap_in_context(_worker)
    set_correlation_id("snap-2")  # mutate AFTER wrapping

    async def _driver():
        return await asyncio.to_thread(wrapped)

    seen = asyncio.run(_driver())
    assert seen == "snap-1"


# ----- wrap_in_context across asyncio.create_task ---------------------------


@pytest.mark.unit
def test_wrap_in_context_propagates_through_create_task():
    """A coroutine wrapped via ``wrap_in_context`` and scheduled via
    ``asyncio.create_task`` observes the correlation id captured at
    wrap time, not whatever the task happens to inherit."""
    set_correlation_id("task-1")

    async def _coro_body():
        return get_correlation_id()

    async def _driver():
        wrapped = wrap_in_context(_coro_body())
        # Mutate AFTER wrapping; the snapshot must hold.
        set_correlation_id("task-2")
        return await asyncio.create_task(wrapped)

    seen = asyncio.run(_driver())
    assert seen == "task-1"


@pytest.mark.unit
def test_wrap_in_context_with_async_callable():
    """``wrap_in_context(async_fn, *args)`` returns a coroutine bound to
    the snapshot."""
    set_correlation_id("async-call")

    async def _coro_body(x):
        return (get_correlation_id(), x)

    async def _driver():
        return await wrap_in_context(_coro_body, 42)

    cid, x = asyncio.run(_driver())
    assert cid == "async-call"
    assert x == 42


@pytest.mark.unit
def test_wrap_in_context_rejects_non_callable_non_coroutine():
    with pytest.raises(TypeError):
        wrap_in_context(42)  # type: ignore[arg-type]


@pytest.mark.unit
def test_wrap_in_context_propagates_other_contextvars_too():
    """The helper snapshots the entire context — every contextvar
    (request user, custom vars, etc.) propagates, not just correlation id.
    """
    set_correlation_id("multi-1")
    set_request_user({"user_id": "u-1"})

    custom_var: contextvars.ContextVar[str] = contextvars.ContextVar(
        "custom_var_test", default="default"
    )
    custom_var.set("custom-value")

    captured: dict = {}

    def _worker():
        from zephyrex.lib.RequestContext import get_request_user

        captured["cid"] = get_correlation_id()
        captured["user"] = get_request_user()
        captured["custom"] = custom_var.get()

    async def _driver():
        await asyncio.to_thread(wrap_in_context(_worker))

    asyncio.run(_driver())

    assert captured["cid"] == "multi-1"
    assert captured["user"] == {"user_id": "u-1"}
    assert captured["custom"] == "custom-value"


@pytest.mark.unit
def test_wrap_in_context_coroutine_propagates_exceptions():
    """An exception raised inside a wrapped coroutine surfaces to the
    awaiter unchanged."""
    set_correlation_id("err-1")

    class _Boom(RuntimeError):
        pass

    async def _coro_body():
        raise _Boom("kaboom")

    async def _driver():
        wrapped = wrap_in_context(_coro_body())
        with pytest.raises(_Boom):
            await wrapped

    asyncio.run(_driver())
