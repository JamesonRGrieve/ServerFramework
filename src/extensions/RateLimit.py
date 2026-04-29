"""
Per-provider rate-limit and concurrency primitives (Item 17).

Single-process, in-memory token bucket and concurrency cap. Multi-instance
deployments must hand off to Item 69's `DistributedCounter` — the
`DistributedRateLimit` stub here marks the integration point.

`parse_retry_after` understands both `Retry-After: <seconds>` and
`Retry-After: <HTTP-date>` per RFC 9110.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Optional


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
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(0, int(delta))
    except (TypeError, ValueError):
        return None


# ----- Distributed stub (Item 69 integration point) -------------------------


class DistributedRateLimit(TokenBucket):
    """Multi-instance rate-limit placeholder.

    TODO: integrate Item 69 (DistributedCounter). For now this is a
    drop-in subclass of `TokenBucket` so callers can wire it into
    provider config; behavior is identical to single-process until the
    distributed-counter primitive lands.
    """

    def __init__(self, rate: RateLimit, counter_key: str) -> None:
        super().__init__(rate)
        self.counter_key = counter_key
        # TODO: integrate Item 69's DistributedCounter here. Until then,
        # behavior is single-process. Document this limitation in PRV.Patterns.md.


__all__ = [
    "RateLimit",
    "TokenBucket",
    "ConcurrencyLimit",
    "DistributedRateLimit",
    "parse_retry_after",
]
