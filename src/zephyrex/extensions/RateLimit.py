"""
Per-provider rate-limit and concurrency primitives (Item 17).

Single-process, in-memory token bucket and concurrency cap.
``DistributedRateLimit`` delegates cross-instance accounting to Item 69's
``DistributedCounter`` so multi-instance deployments share state through
the same primitive that backs ``Quota`` (Item 19) and the per-tenant
fairness scheduler (Item 57).

The DB-backed variant of ``DistributedCounter`` requires a session
provider (see ``PostgresDistributedCounter``); wiring that into the
provider config is a separate operator-facing concern. By default
``DistributedRateLimit`` uses the in-memory backend, which is correct
for single-process and degrades to advisory in a multi-instance
deployment until the DB session provider is supplied.

``parse_retry_after`` understands both ``Retry-After: <seconds>`` and
``Retry-After: <HTTP-date>`` per RFC 9110.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

from zephyrex.lib.DateTimeUtils import ensure_utc

from zephyrex.lib.DistributedCounter import (
    DistributedCounter,
    InMemoryDistributedCounter,
)

# ----- Token bucket ---------------------------------------------------------


@dataclass(frozen=True)
class RateLimit:
    """Steady-state RPS plus burst capacity. Provider authors set this on
    their `AbstractStaticProvider` subclass."""

    rps: float
    burst: int = 10


class TokenBucket:
    """Classic token-bucket implementation.

    Tokens accrue at `rate.rps` per second up to `rate.burst`. Acquire
    blocks (with optional timeout) or returns False on `try_acquire`.
    Single-process, in-memory; thread-safe.
    """

    def __init__(self, rate: RateLimit) -> None:
        if rate.rps <= 0:
            raise ValueError("RateLimit.rps must be positive")
        if rate.burst <= 0:
            raise ValueError("RateLimit.burst must be positive")
        self._rate = rate
        self._tokens: float = float(rate.burst)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(self._rate.burst, self._tokens + elapsed * self._rate.rps)
        self._last_refill = now

    def try_acquire(self, n: int = 1) -> bool:
        """Non-blocking. Returns True on success, False if no token available."""
        with self._lock:
            self._refill_locked()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    def acquire_blocking(self, timeout: Optional[float] = None, n: int = 1) -> bool:
        """Block until a token is available or `timeout` elapses.

        Returns True on success, False on timeout. `timeout=None` waits forever.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            while True:
                self._refill_locked()
                if self._tokens >= n:
                    self._tokens -= n
                    return True
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                else:
                    remaining = None
                # Sleep until enough tokens *should* exist.
                needed = n - self._tokens
                wait = needed / self._rate.rps
                if remaining is not None:
                    wait = min(wait, remaining)
                self._cv.wait(timeout=wait)

    @property
    def tokens(self) -> float:
        with self._lock:
            self._refill_locked()
            return self._tokens


# ----- Concurrency cap ------------------------------------------------------


class ConcurrencyLimit:
    """Per-provider concurrency cap. Wraps a `threading.Semaphore`.

    Use as a context manager: ``with limit: ...``. `acquire(timeout)`
    returns True/False to allow caller-side fallback handling.
    """

    def __init__(self, max_concurrent: int) -> None:
        if max_concurrent <= 0:
            raise ValueError("ConcurrencyLimit.max_concurrent must be positive")
        self._max = max_concurrent
        self._sem = threading.BoundedSemaphore(max_concurrent)

    def acquire(self, timeout: Optional[float] = None) -> bool:
        if timeout is None:
            self._sem.acquire()
            return True
        return self._sem.acquire(timeout=timeout)

    def release(self) -> None:
        self._sem.release()

    def __enter__(self) -> "ConcurrencyLimit":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    @property
    def max_concurrent(self) -> int:
        return self._max


# ----- Retry-After parsing --------------------------------------------------


def parse_retry_after(header_value: Optional[str]) -> Optional[int]:
    """Parse a `Retry-After` header value.

    Per RFC 9110 the value is either a non-negative integer number of
    seconds or an HTTP-date. Returns the delay in whole seconds, or
    None if the header is missing or malformed. Negative or past dates
    return 0.
    """
    if not header_value:
        return None
    s = header_value.strip()
    if s.isdigit():
        return max(0, int(s))
    try:
        dt = parsedate_to_datetime(s)
        if dt is None:
            return None
        dt = ensure_utc(dt)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(delta))
    except (TypeError, ValueError):
        return None


# ----- Distributed (Item 69 — DistributedCounter integration) ---------------


def _run_async(coro) -> bool:
    """Drive a DistributedCounter coroutine from sync code.

    Rate-limit acquisition is invoked from synchronous provider HTTP
    clients; ``DistributedCounter.try_consume`` returns a coroutine in the
    per-counter shape. Use ``asyncio.run`` when no loop is active, and
    ``loop.run_until_complete`` when we are already inside one (only
    happens during tests that exercise async paths). The fallback returns
    ``True`` so a misconfigured event-loop state is permissive rather
    than blocking — the in-memory ``TokenBucket`` check upstream is
    still the authoritative gate for single-process callers.
    """
    try:
        return bool(asyncio.run(coro))
    except RuntimeError:
        try:
            loop = asyncio.get_event_loop()
            return bool(loop.run_until_complete(coro))
        except RuntimeError:
            return True


class DistributedRateLimit(TokenBucket):
    """Cross-instance rate-limit anchored on Item 69's ``DistributedCounter``.

    The local ``TokenBucket`` continues to bound per-process burst /
    rate-of-flow (where the counter primitive has no notion). The counter
    coordinates the *fixed limit per period* across instances: every
    successful local acquire records consumption against a shared
    ``(counter_key, period_key)`` so peer instances see updated state.
    A failed counter consume rolls the local tokens back so single-
    process semantics still hold.

    By default the in-memory backend is used, which is correct for a
    single-process deployment and acts as advisory bookkeeping in
    multi-instance setups. Inject a Postgres-backed counter via
    ``counter`` for true cross-instance authoritativeness.
    """

    def __init__(
        self,
        rate: RateLimit,
        counter_key: str,
        period_key: Optional[str] = None,
        counter: Optional[DistributedCounter] = None,
    ) -> None:
        super().__init__(rate)
        self.counter_key = counter_key
        self._counter = counter or InMemoryDistributedCounter(
            key=counter_key,
            limit=rate.burst,
            period_key=period_key,
        )

    def try_acquire(self, n: int = 1) -> bool:
        if not super().try_acquire(n):
            return False
        if not _run_async(self._counter.try_consume(amount=n)):
            # Counter says no headroom — give the local tokens back so the
            # single-process invariant (tokens spent ⇔ work admitted) holds.
            with self._lock:
                self._tokens = min(self._rate.burst, self._tokens + n)
            return False
        return True

    # TODO(operator): the in-memory counter is per-process. To get true
    # cross-instance accounting, construct a ``PostgresDistributedCounter``
    # with a ``_session_provider`` callable that yields a SQLAlchemy
    # session against the ``distributed_counter`` table (schema in
    # ``lib/DistributedCounter.py`` module docstring) and pass it as
    # the ``counter=`` kwarg. The framework does not pick a session
    # source on the operator's behalf because rate-limit state may live
    # on a different DB than application data (e.g., a dedicated
    # ratelimit Postgres instance to avoid contention with mainline
    # writes).


__all__ = [
    "RateLimit",
    "TokenBucket",
    "ConcurrencyLimit",
    "DistributedRateLimit",
    "parse_retry_after",
]
