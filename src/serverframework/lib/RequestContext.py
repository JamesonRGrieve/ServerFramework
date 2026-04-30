import asyncio
import contextvars
import functools
import inspect
import time
import uuid
from contextvars import ContextVar
from typing import Any, Callable, Coroutine, Dict, Optional, Union

# Context variable to store current request's user information
_request_user_context: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "request_user_context", default=None
)

# Item 47 — per-request deadline budget (monotonic time at which the
# inbound request expires). Set by middleware from `X-Request-Timeout-Ms`
# header at ingress; downstream layers (rotation, HTTP client, DB,
# event bus) call `remaining_deadline_ms()` to scope their own timeouts.
_request_deadline: ContextVar[Optional[float]] = ContextVar(
    "request_deadline", default=None
)

# Item 85 — per-request correlation id used to stitch together every
# log line for a single inbound request. Derived at ingress either from
# the W3C ``traceparent`` header (the 32-hex trace-id segment) or minted
# as a fresh ``uuid4().hex`` when no traceparent is present. Propagates
# across ``asyncio.to_thread`` and ``asyncio.create_task`` boundaries via
# :func:`wrap_in_context`.
_request_correlation_id: ContextVar[Optional[str]] = ContextVar(
    "request_correlation_id", default=None
)


class DeadlineExceededError(Exception):
    """Item 47 — raised by framework I/O layers when the per-request budget
    has expired. Surfaces as HTTP 504 in the inbound surface; downstream
    layers should re-raise rather than swallow.

    Attributes:
        elapsed_ms: integer milliseconds elapsed since the deadline was set
        layer: human-readable name of the layer that detected the expiry
            (e.g. ``"rotation"``, ``"provider_http"``, ``"db"``)
    """

    def __init__(self, elapsed_ms: int, layer: str = "unknown"):
        self.elapsed_ms = elapsed_ms
        self.layer = layer
        super().__init__(
            f"Request deadline exceeded after {elapsed_ms}ms in layer={layer}"
        )


def set_request_user(user_info: Dict[str, Any]) -> None:
    """Set the current request's user information"""
    _request_user_context.set(user_info)


def get_request_user() -> Optional[Dict[str, Any]]:
    """Get the current request's user information"""
    return _request_user_context.get()


def get_user_timezone() -> str:
    """Get the current user's timezone or default to UTC"""
    user_info = get_request_user()
    if user_info and "timezone" in user_info:
        return user_info["timezone"]
    return "UTC"


def set_request_deadline_ms(timeout_ms: Optional[int]) -> None:
    """Item 47 — set the per-request deadline from a relative
    (gRPC-style) timeout in milliseconds. ``None`` clears the deadline."""
    if timeout_ms is None or timeout_ms <= 0:
        _request_deadline.set(None)
        return
    _request_deadline.set(time.monotonic() + (timeout_ms / 1000.0))


def get_request_deadline() -> Optional[float]:
    """Return the current request's monotonic-time deadline, or None."""
    return _request_deadline.get()


def remaining_deadline_ms() -> Optional[int]:
    """Item 47 — return the remaining budget in milliseconds, or None
    when no deadline is set. Downstream layers compute their own timeout
    as ``min(layer_default, remaining_deadline_ms())``.

    A non-positive value indicates the budget is exhausted; callers
    should raise :class:`DeadlineExceededError` rather than scheduling
    further work.
    """
    deadline = _request_deadline.get()
    if deadline is None:
        return None
    remaining = (deadline - time.monotonic()) * 1000.0
    return int(remaining) if remaining > 0 else 0


def check_deadline_or_raise(layer: str = "unknown") -> None:
    """Item 47 — fail fast when the budget is exhausted. Called by
    framework layers before scheduling I/O so that no further wall-clock
    is consumed past the SLA boundary.
    """
    remaining = remaining_deadline_ms()
    if remaining is None:
        return
    if remaining <= 0:
        raise DeadlineExceededError(elapsed_ms=-remaining if remaining < 0 else 0, layer=layer)


# ----- Item 85 — correlation id ---------------------------------------------


def set_correlation_id(value: Optional[str]) -> None:
    """Item 85 — set the per-request correlation id. ``None`` clears it.

    Inbound middleware derives the value at ingress: when a W3C
    ``traceparent`` header is present, the 32-hex trace-id segment is used
    so log lines correlate with distributed-tracing backends; otherwise a
    fresh ``uuid4().hex`` is minted so every request still has an id.
    """
    _request_correlation_id.set(value)


def get_correlation_id() -> Optional[str]:
    """Item 85 — return the current request's correlation id, or None
    when no inbound request is active (background workers, startup, etc.).
    """
    return _request_correlation_id.get()


def clear_correlation_id() -> None:
    """Item 85 — explicitly clear the correlation id. Folded into
    :func:`clear_request_context`; exposed for symmetry with the
    setters/getters and for tests that want to scrub a single field."""
    _request_correlation_id.set(None)


def mint_correlation_id() -> str:
    """Item 85 — return a fresh hex correlation id. Inbound middleware
    uses this when no ``traceparent`` header is present."""
    return uuid.uuid4().hex


def wrap_in_context(
    target: Union[Callable[..., Any], Coroutine[Any, Any, Any]],
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Item 85 — snapshot the current contextvars and bind them around a
    coroutine or callable so the correlation id (and every other
    contextvar) propagates across ``asyncio.to_thread`` /
    ``asyncio.create_task`` boundaries.

    The helper accepts either:

    * a coroutine object — returns a new coroutine that, when awaited,
      runs the original to completion under the snapshotted context.
      Suitable for ``asyncio.create_task(wrap_in_context(my_coro()))``.
    * a sync callable + ``*args`` / ``**kwargs`` — returns a zero-arg
      callable that, when invoked, runs the function under the snapshot.
      Suitable for
      ``asyncio.to_thread(wrap_in_context(fn, *args, **kwargs))``.
    * an async callable + ``*args`` / ``**kwargs`` — returns a coroutine
      that runs the function under the snapshot.

    Why a snapshot is needed: ``asyncio.to_thread`` runs the callable in
    a worker thread; we use :meth:`contextvars.Context.run` to bind the
    snapshot explicitly so the correlation id reaches the worker even
    when a custom executor or older Python bypasses PEP 567 inheritance.
    For coroutines the snapshot is bound by stepping the coroutine
    inside ``ctx.run`` so each resumption observes the same contextvars.
    """
    ctx = contextvars.copy_context()

    # Coroutine object — driver that steps the coroutine under the snapshot.
    if inspect.iscoroutine(target):
        return _drive_coro_in_context(ctx, target)

    if not callable(target):
        raise TypeError(
            "wrap_in_context expected a coroutine or callable; "
            f"got {type(target).__name__}"
        )

    # Async callable: return a coroutine that drives it under the snapshot.
    if inspect.iscoroutinefunction(target):
        coro = target(*args, **kwargs)  # type: ignore[misc]
        return _drive_coro_in_context(ctx, coro)

    # Sync callable: return a zero-arg callable to feed asyncio.to_thread.
    @functools.wraps(target)  # type: ignore[arg-type]
    def _runner_sync() -> Any:
        return ctx.run(target, *args, **kwargs)  # type: ignore[arg-type]

    return _runner_sync


async def _drive_coro_in_context(
    ctx: contextvars.Context, coro: Coroutine[Any, Any, Any]
) -> Any:
    """Step ``coro`` to completion with each ``send``/``throw`` executed
    inside ``ctx``. Mirrors :class:`asyncio.Task`'s contextvar binding
    semantics so explicit users get the same guarantees regardless of
    whether the coroutine is later wrapped in a task or awaited directly.
    """
    value: Any = None
    exc: Optional[BaseException] = None
    while True:
        try:
            if exc is not None:
                to_send = exc
                exc = None
                yielded = ctx.run(coro.throw, to_send)
            else:
                yielded = ctx.run(coro.send, value)
        except StopIteration as stop:
            return stop.value
        try:
            value = await yielded
        except BaseException as e:
            exc = e


def clear_request_context() -> None:
    """Clear the request context"""
    _request_user_context.set(None)
    _request_deadline.set(None)
    _request_correlation_id.set(None)
