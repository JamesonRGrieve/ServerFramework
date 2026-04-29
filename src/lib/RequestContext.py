import time
from contextvars import ContextVar
from typing import Optional, Dict, Any

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


def clear_request_context() -> None:
    """Clear the request context"""
    _request_user_context.set(None)
    _request_deadline.set(None)
