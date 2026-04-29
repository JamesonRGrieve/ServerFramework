"""
Inbound surface security primitives.

Item 71 — CORS validation, inbound rate-limiting decorator, and brute-force
lockout policy.

This module is the inbound counterpart to outbound security primitives:
- CORS configuration validators that fail fast in production with insecure
  combinations (`*` origin + `allow_credentials=True`, or `*` in production).
- A `@rate_limit("100/min", scope="ip")` decorator that annotates callable
  endpoints with rate-limit metadata. Enforcement consumes Item 69's
  `DistributedCounter` when available; falls back to a per-process in-memory
  counter when not.
- A `LockoutPolicy` dataclass and an in-memory `LockoutTracker` per
  `(actor_key, flow)` for brute-force protection on auth flows.
- A pluggable `AnomalyDetector` ABC (default `NoOpAnomalyDetector`) so each
  auth flow can hand off failure events without inventing a captcha or SIEM
  integration on the spot.

Production deployments that need cross-process consistency for the lockout
tracker must wire the `DistributedCounter` from Item 69 — see the docstring
on `LockoutTracker`.
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import RLock
from typing import (
    Any,
    Callable,
    Deque,
    Dict,
    List,
    Literal,
    Optional,
    Tuple,
    TypeVar,
)

try:
    from lib.Logging import logger
except ImportError:  # pragma: no cover — fallback for very-early bootstrap
    import logging

    logger = logging.getLogger(__name__)


__all__ = [
    "CORSPolicyError",
    "RateLimitExceeded",
    "validate_cors_config",
    "parse_cors_origins",
    "rate_limit",
    "parse_rate_spec",
    "RateLimitScope",
    "LockoutPolicy",
    "LockoutTracker",
    "AnomalyDetector",
    "NoOpAnomalyDetector",
    "DEFAULT_AUTH_RATE_LIMIT",
    "DEFAULT_MUTATING_RATE_LIMIT",
    "DEFAULT_READ_RATE_LIMIT",
]


# ---------------------------------------------------------------------------
# Documented per-endpoint-shape defaults.
#
# Operators may override on a per-route basis via `@rate_limit(...)`; these
# constants exist so each new endpoint does not invent a fresh limit.
# ---------------------------------------------------------------------------

DEFAULT_AUTH_RATE_LIMIT = "10/min"
DEFAULT_MUTATING_RATE_LIMIT = "60/min"
DEFAULT_READ_RATE_LIMIT = "600/min"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CORSPolicyError(ValueError):
    """Raised when a CORS configuration is rejected by `validate_cors_config`.

    Subclass of `ValueError` so existing FastAPI startup-error handlers
    surface a 500 with a clear message. Operators should fix the configured
    `APP_CORS_ALLOWED_ORIGINS` env var or `APP_ENV` rather than catching this.
    """


class RateLimitExceeded(Exception):
    """Raised by enforcement code when a `@rate_limit` budget is exhausted.

    Carries `retry_after_seconds` so the FastAPI middleware can render a 429
    with a `Retry-After` header.
    """

    def __init__(
        self,
        message: str = "Rate limit exceeded",
        *,
        retry_after_seconds: Optional[float] = None,
        scope: Optional[str] = None,
        actor_key: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after_seconds = retry_after_seconds
        self.scope = scope
        self.actor_key = actor_key


# ---------------------------------------------------------------------------
# CORS validation (Item 71a)
# ---------------------------------------------------------------------------


_URL_SHAPE = re.compile(r"^https?://[A-Za-z0-9._\-]+(:[0-9]+)?(/.*)?$")


def parse_cors_origins(env_value: str) -> List[str]:
    """Split a comma-separated `APP_CORS_ALLOWED_ORIGINS` env value.

    Trims whitespace, drops empty entries, and validates that each non-`*`
    origin parses as `scheme://host[:port]`. Raises `CORSPolicyError` on
    a malformed entry; this is fail-fast at startup, not request-time.
    """
    if env_value is None:
        return []

    parts = [p.strip() for p in env_value.split(",")]
    parts = [p for p in parts if p]

    for part in parts:
        if part == "*":
            continue
        if not _URL_SHAPE.match(part):
            raise CORSPolicyError(
                f"Malformed CORS origin {part!r}. "
                "Expected scheme://host[:port] (e.g., https://app.example.com)."
            )
    return parts


def validate_cors_config(
    allow_origins: List[str],
    allow_credentials: bool,
    app_env: str,
) -> None:
    """Reject insecure CORS combinations at app boot.

    Two explicit denials, in priority order:

    1. `allow_credentials=True` with `*` in `allow_origins` is RFC-violating
       — browsers will treat the response as `null` origin regardless. Reject
       in every environment.
    2. `app_env == "production"` with `*` in `allow_origins` is rejected even
       without credentials, because production deployments must declare an
       explicit allowlist (`APP_CORS_ALLOWED_ORIGINS`).

    Development environments may continue to use `*` without credentials and
    receive only a startup warning (caller responsibility to log).
    """
    has_wildcard = "*" in (allow_origins or [])

    if allow_credentials and has_wildcard:
        raise CORSPolicyError(
            "CORS misconfiguration: allow_credentials=True is incompatible "
            "with allow_origins=['*']. Browsers will treat the response as "
            "Origin: null. Configure APP_CORS_ALLOWED_ORIGINS with an "
            "explicit comma-separated allowlist."
        )

    if app_env == "production" and has_wildcard:
        raise CORSPolicyError(
            "CORS misconfiguration: APP_ENV=production refuses to start with "
            "allow_origins=['*']. Configure APP_CORS_ALLOWED_ORIGINS with an "
            "explicit comma-separated allowlist of trusted origins."
        )


# ---------------------------------------------------------------------------
# Rate-limit decorator (Item 71b)
# ---------------------------------------------------------------------------


RateLimitScope = Literal[
    "ip",
    "user",
    "tenant",
    "(ip, endpoint)",
    "(user, endpoint)",
]


_UNIT_SECONDS = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
}

_RATE_SPEC = re.compile(r"^\s*(\d+)\s*/\s*([A-Za-z]+)\s*$")


def parse_rate_spec(spec: str) -> Tuple[int, int]:
    """Parse a `"100/min"` style rate spec into `(count, window_seconds)`.

    Accepts the units recognized in `_UNIT_SECONDS`. Raises `ValueError` on
    malformed input rather than silently defaulting — the wrong number of
    requests per second is a security issue worth a startup error.
    """
    match = _RATE_SPEC.match(spec or "")
    if not match:
        raise ValueError(
            f"Malformed rate spec {spec!r}. Expected '<count>/<unit>' "
            "(e.g., '100/min', '10/sec', '600/h')."
        )
    count = int(match.group(1))
    unit = match.group(2).lower()
    if unit not in _UNIT_SECONDS:
        raise ValueError(
            f"Unknown rate-spec unit {unit!r}. Known units: "
            f"{sorted(set(_UNIT_SECONDS.keys()))}"
        )
    if count <= 0:
        raise ValueError(f"Rate-spec count must be > 0, got {count}.")
    return count, _UNIT_SECONDS[unit]


F = TypeVar("F", bound=Callable[..., Any])


def rate_limit(spec: str, scope: RateLimitScope = "ip") -> Callable[[F], F]:
    """Annotate a callable with rate-limit metadata.

    The decorator itself does not perform enforcement — it stamps the
    callable with `_rate_limit_spec` and `_rate_limit_scope` attributes
    that downstream FastAPI middleware (or a `RouterMixin` wrapper)
    consumes. Enforcement walks Item 69's `DistributedCounter` when
    available; absent that, the in-process `_InMemoryCounter` provides
    best-effort single-process semantics so tests do not need a Redis.

    Example:
        @rate_limit("10/min", scope="ip")
        async def login(request, body): ...

    Scopes are documented as Literal types; passing an unknown scope is
    accepted by the decorator (it is forwarded to enforcement) but will
    fail at enforcement-time when the scope cannot be resolved to an
    actor key.
    """
    # Validate the spec at decoration time, not at first request.
    count, window_seconds = parse_rate_spec(spec)

    def _decorator(fn: F) -> F:
        setattr(fn, "_rate_limit_spec", spec)
        setattr(fn, "_rate_limit_count", count)
        setattr(fn, "_rate_limit_window_seconds", window_seconds)
        setattr(fn, "_rate_limit_scope", scope)
        return fn

    return _decorator


# ---------------------------------------------------------------------------
# Distributed-counter integration with in-memory fallback
# ---------------------------------------------------------------------------


class _InMemoryCounter:
    """Fallback per-process counter used when Item 69's `DistributedCounter`
    is not yet available.

    Keeps a deque of timestamps per `(scope_actor_key)` and prunes entries
    older than `window_seconds` on each `incr`. Single-process only — multi-
    worker deployments that need cross-process consistency must wire the
    distributed implementation.
    """

    def __init__(self) -> None:
        self._buckets: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = RLock()

    def incr(self, key: str, window_seconds: int) -> int:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            bucket = self._buckets[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            bucket.append(now)
            return len(bucket)

    def reset(self, key: Optional[str] = None) -> None:
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)


_inmemory_counter = _InMemoryCounter()


def _get_counter() -> Any:
    """Return Item 69's `DistributedCounter` if available, else in-memory.

    Imported lazily so the module loads cleanly even before Batch C lands
    `lib/DistributedCounter.py`.
    """
    try:  # pragma: no cover — exercised only when the module is present
        from lib.DistributedCounter import DistributedCounter

        return DistributedCounter
    except Exception:
        return _inmemory_counter


# ---------------------------------------------------------------------------
# Lockout policy and tracker (Item 71c)
# ---------------------------------------------------------------------------


@dataclass
class LockoutPolicy:
    """Configuration for brute-force lockout on a per-(actor, flow) basis.

    Attributes:
        failures_per_window: Number of failures within `window_seconds`
            that trigger a lockout. Default 5.
        window_seconds: Sliding window size for failure counting. Default
            900 (15 minutes).
        lockout_seconds: How long the actor is locked out once tripped.
            Default 1800 (30 minutes).
    """

    failures_per_window: int = 5
    window_seconds: int = 900
    lockout_seconds: int = 1800


class LockoutTracker:
    """Tracks failure counts and lockout state per `(actor_key, flow)`.

    In-memory implementation backed by a deque of timestamps per actor.
    A failed call to `record_failure` appends the current timestamp,
    prunes entries older than `window_seconds`, and (if the count
    crosses `failures_per_window`) sets a `_locked_until` timestamp.

    Production deployments that need cross-process consistency should
    swap this for a backing store driven by Item 69's distributed
    counter — the call surface (`record_failure` / `is_locked` / `clear`)
    is intentionally minimal so the swap is mechanical.
    """

    def __init__(self, policy: Optional[LockoutPolicy] = None) -> None:
        self.policy = policy or LockoutPolicy()
        self._failures: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._locked_until: Dict[Tuple[str, str], float] = {}
        self._lock = RLock()

    def record_failure(self, actor_key: str, flow: str) -> None:
        """Record a single failed attempt.

        Trips the lockout when the count of failures within the policy's
        sliding window crosses `failures_per_window`. The lockout remains
        in effect for `lockout_seconds` from the trip moment.
        """
        if not actor_key or not flow:
            # Defensive: refuse to silently accept missing keys; an empty
            # actor would coalesce every actor's failures into one bucket.
            raise ValueError("actor_key and flow must be non-empty strings")

        now = time.monotonic()
        cutoff = now - self.policy.window_seconds
        key = (actor_key, flow)
        with self._lock:
            bucket = self._failures[key]
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            bucket.append(now)
            if len(bucket) >= self.policy.failures_per_window:
                self._locked_until[key] = now + self.policy.lockout_seconds
                logger.warning(
                    "Lockout tripped",
                    extra={
                        "actor_key": actor_key,
                        "flow": flow,
                        "failures": len(bucket),
                        "lockout_seconds": self.policy.lockout_seconds,
                    },
                )

    def is_locked(self, actor_key: str, flow: str) -> bool:
        """Return True if `(actor_key, flow)` is currently locked out."""
        key = (actor_key, flow)
        now = time.monotonic()
        with self._lock:
            until = self._locked_until.get(key)
            if until is None:
                return False
            if until <= now:
                # Lockout window passed. Eagerly evict so subsequent
                # calls do not see stale state.
                self._locked_until.pop(key, None)
                self._failures.pop(key, None)
                return False
            return True

    def remaining_lockout_seconds(
        self, actor_key: str, flow: str
    ) -> Optional[float]:
        """Return seconds remaining on an active lockout, or None."""
        key = (actor_key, flow)
        now = time.monotonic()
        with self._lock:
            until = self._locked_until.get(key)
            if until is None or until <= now:
                return None
            return until - now

    def clear(self, actor_key: str, flow: str) -> None:
        """Reset counters and any active lockout for this actor/flow.

        Called on a successful authentication so a user who misremembered
        their password once does not carry that failure into the next
        legitimate attempt's window.
        """
        key = (actor_key, flow)
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)


# ---------------------------------------------------------------------------
# Anomaly detector (Item 71c integration point)
# ---------------------------------------------------------------------------


class AnomalyDetector(ABC):
    """Pluggable hook called on every recorded auth-flow failure.

    Concrete implementations integrate with captcha (hCaptcha, reCAPTCHA),
    step-up MFA (reuses Item 18's freshness gate), or SIEM alerting (reuses
    Item 85's `ErrorReporter`). Each auth flow calls `report_failure` after
    `LockoutTracker.record_failure`; the detector decides whether to escalate.
    """

    @abstractmethod
    def report_failure(self, actor_key: str, flow: str) -> None:
        """Report a single failure event."""


class NoOpAnomalyDetector(AnomalyDetector):
    """Default detector that records the failure and does nothing else.

    Used when no captcha / step-up / SIEM is wired. Logs at debug level so
    operators can confirm the integration point fires without flooding logs.
    """

    def report_failure(self, actor_key: str, flow: str) -> None:
        logger.debug(
            "Auth-flow failure recorded (no-op anomaly detector)",
            extra={"actor_key": actor_key, "flow": flow},
        )
