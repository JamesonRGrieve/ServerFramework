"""Read-replica routing primitives (Item 54).

Two opt-in mechanisms route read-only operations to a configured
replica:
1. @read_only decorator on BLL methods declares the method is safe
   to route to a replica.
2. RequestContext.read_only: bool flag forces all session binding
   within the request to use replicas.

Read-after-write consistency is preserved: once a write has occurred
against primary in the same logical request, subsequent reads bind
primary regardless of @read_only annotation.

Configuration (env-var driven):
- DB_REPLICA_URLS: comma-separated list of replica connection strings.
  Empty (default) disables replica routing; reads bind primary.

Health: each replica is health-checked at a configurable interval;
unhealthy replicas are removed from rotation until they recover.

L7 load shaping (round-robin, weighted, latency-based) is delegated
to HAProxy or the database's own load balancer per Item 3.
"""

import functools
from contextvars import ContextVar
from typing import Any, Callable, List, Optional

# Per-request flag set by middleware or by the caller; trumps method-
# level annotations. Default False = use primary.
_read_only_var: ContextVar[bool] = ContextVar("_read_only", default=False)

# Per-request "we've already written to primary" sticky flag; once set,
# subsequent reads in the same request bind primary regardless of
# @read_only or RequestContext.read_only.
_primary_write_seen_var: ContextVar[bool] = ContextVar("_primary_write_seen", default=False)


def set_read_only(value: bool) -> None:
    _read_only_var.set(value)


def get_read_only() -> bool:
    return _read_only_var.get()


def mark_primary_write_seen() -> None:
    """Called by the session binder when a write occurs against primary."""
    _primary_write_seen_var.set(True)


def primary_write_seen() -> bool:
    return _primary_write_seen_var.get()


def should_route_to_replica() -> bool:
    """Decision rule: route to replica iff read_only is set AND no
    primary write has occurred in this request."""
    return get_read_only() and not primary_write_seen()


def read_only(fn: Callable) -> Callable:
    """Mark a BLL method as safe to route to a replica.

    The session binder consults the marker at dispatch time and binds
    a replica session when one is configured AND the request hasn't
    yet written to primary.

    Usage::

        class UserManager(AbstractBLLManager):
            @read_only
            def list_active(self): ...
    """
    fn._read_only = True  # type: ignore[attr-defined]

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        token = _read_only_var.set(True)
        try:
            return fn(*args, **kwargs)
        finally:
            _read_only_var.reset(token)

    @functools.wraps(fn)
    async def async_wrapper(*args, **kwargs):
        token = _read_only_var.set(True)
        try:
            return await fn(*args, **kwargs)
        finally:
            _read_only_var.reset(token)

    import asyncio
    if asyncio.iscoroutinefunction(fn):
        async_wrapper._read_only = True  # type: ignore[attr-defined]
        return async_wrapper
    return wrapper


def is_read_only(fn: Callable) -> bool:
    return getattr(fn, "_read_only", False)


# Replica-pool registry (parsed from env vars at startup; populated by
# the session binder).
class ReplicaPool:
    """Round-robin replica selector; health-aware."""

    def __init__(self, urls: List[str]) -> None:
        self._urls = list(urls)
        self._next = 0
        self._unhealthy: set = set()

    def next_url(self) -> Optional[str]:
        """Return the next healthy replica URL, or None if no replicas
        are configured / all are unhealthy. Caller falls back to
        primary on None."""
        if not self._urls:
            return None
        for _ in range(len(self._urls)):
            url = self._urls[self._next]
            self._next = (self._next + 1) % len(self._urls)
            if url not in self._unhealthy:
                return url
        return None

    def mark_unhealthy(self, url: str) -> None:
        self._unhealthy.add(url)

    def mark_healthy(self, url: str) -> None:
        self._unhealthy.discard(url)


__all__ = [
    "ReplicaPool",
    "get_read_only",
    "is_read_only",
    "mark_primary_write_seen",
    "primary_write_seen",
    "read_only",
    "set_read_only",
    "should_route_to_replica",
]
