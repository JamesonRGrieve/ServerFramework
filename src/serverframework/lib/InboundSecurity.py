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

import ipaddress
import os
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


def _trusted_proxy_networks() -> List[ipaddress._BaseNetwork]:
    """Parse TRUSTED_PROXIES into a list of ip_network objects. Empty unless
    explicitly configured. Wildcards and malformed entries are rejected.
    """
    raw = (os.environ.get("TRUSTED_PROXIES") or "").strip()
    if not raw:
        return []
    nets: List[ipaddress._BaseNetwork] = []
    for part in (p.strip() for p in raw.split(",") if p.strip()):
        if part == "*" or "*" in part:
            raise ValueError(
                f"TRUSTED_PROXIES rejects wildcard entries: {part!r}. "
                "Use explicit CIDRs (e.g. 10.0.0.0/8)."
            )
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError as e:
            raise ValueError(f"TRUSTED_PROXIES entry {part!r}: {e}") from e
    return nets


def _peer_is_trusted_proxy(peer_host: Optional[str]) -> bool:
    """Return True iff the immediate connection peer is in TRUSTED_PROXIES."""
    if not peer_host:
        return False
    try:
        peer = ipaddress.ip_address(peer_host)
    except ValueError:
        return False
    for net in _trusted_proxy_networks():
        if peer in net:
            return True
    return False

try:
    from serverframework.lib.Logging import logger
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
    "RateLimitMiddleware",
    "register_rate_limited_route",
    "reset_rate_limit_state",
    "discover_rate_limited_routes",
    "set_rate_limit_counter",
    "LockoutPolicy",
    "LockoutTracker",
    "AnomalyDetector",
    "NoOpAnomalyDetector",
    "DEFAULT_AUTH_RATE_LIMIT",
    "DEFAULT_MUTATING_RATE_LIMIT",
    "DEFAULT_READ_RATE_LIMIT",
    "compare_api_key",
    "resolve_principal_from_api_key",
    "resolve_client_ip",
    "SecurityHeadersMiddleware",
    "BodySizeLimitMiddleware",
    "DEFAULT_MAX_BODY_BYTES",
]


# H-4 — strict response-header defaults for an HTTP JSON API. Operators
# override per deployment via the ``SECURITY_HEADERS_*`` env vars; defaults
# are conservative because the alternative is shipping permissive values
# baked into core. CSP is intentionally narrow (``default-src 'none'``) —
# this surface is JSON, not HTML; pages that need CSP relaxation should
# either declare their own header at the route level or adjust the env.
_DEFAULT_SECURITY_HEADERS: Dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "interest-cohort=()",
    "Content-Security-Policy": (
        "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
    ),
    # HSTS is only meaningful over TLS; the middleware only emits it when
    # ``APP_ENV=production`` (see ``SecurityHeadersMiddleware``).
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


# M-1 — default cap for request bodies. 10 MiB is large enough for typical
# JSON payloads and small file-uploads; larger uploads must opt out
# explicitly per route (e.g. via a streaming-multipart provider in an
# extension). Configurable via ``MAX_REQUEST_BODY_BYTES``.
DEFAULT_MAX_BODY_BYTES: int = 10 * 1024 * 1024


class SecurityHeadersMiddleware:
    """ASGI middleware that injects strict response headers (H-4).

    Mounted unconditionally by ``app.py``. Defaults come from
    ``_DEFAULT_SECURITY_HEADERS``; operators can override individual
    values via env vars of the form ``SECURITY_HEADER_<UPPER_NAME>``
    (e.g. ``SECURITY_HEADER_CONTENT_SECURITY_POLICY``). HSTS is only
    emitted in production because dev/test deployments commonly run
    over plain HTTP and a Strict-Transport-Security header on an HTTP
    response is ignored anyway but introduces deployment foot-guns.
    """

    def __init__(self, app: Any) -> None:
        self.app = app
        self._headers = self._resolve_headers()
        self._strip_server = (
            os.environ.get("SECURITY_STRIP_SERVER_HEADER", "1") != "0"
        )

    @staticmethod
    def _resolve_headers() -> Dict[str, str]:
        headers = dict(_DEFAULT_SECURITY_HEADERS)
        if os.environ.get("APP_ENV", "").lower() != "production":
            headers.pop("Strict-Transport-Security", None)
        for name in list(headers.keys()):
            override = os.environ.get(
                "SECURITY_HEADER_" + name.upper().replace("-", "_")
            )
            if override is not None:
                if override == "":
                    headers.pop(name, None)
                else:
                    headers[name] = override
        return headers

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers_to_add = self._headers
        strip_server = self._strip_server

        async def send_wrapper(message):
            if message.get("type") == "http.response.start":
                raw = list(message.get("headers") or [])
                existing_lower = {k.decode("latin-1").lower() for k, _ in raw}
                if strip_server:
                    raw = [(k, v) for k, v in raw if k.decode("latin-1").lower() != "server"]
                for name, value in headers_to_add.items():
                    if name.lower() in existing_lower:
                        continue
                    raw.append(
                        (name.encode("latin-1"), value.encode("latin-1"))
                    )
                message["headers"] = raw
            await send(message)

        await self.app(scope, receive, send_wrapper)


class BodySizeLimitMiddleware:
    """ASGI middleware that enforces a max request body size (M-1).

    Two enforcement paths:
      1. Trust ``Content-Length`` when present; reject the request with
         ``413 Payload Too Large`` before forwarding to the app.
      2. For chunked / unknown-length bodies, count bytes as they stream
         in via ``receive`` and reject if the running total exceeds the
         cap.

    The cap is configurable via ``MAX_REQUEST_BODY_BYTES``. Set the value
    to ``0`` (or any non-positive integer) to disable enforcement; this
    is the escape hatch for routes that intentionally stream very large
    objects (object-storage providers, federation mirrors). The default
    is ``DEFAULT_MAX_BODY_BYTES``.
    """

    def __init__(self, app: Any, max_bytes: Optional[int] | None = None) -> None:
        self.app = app
        if max_bytes is None:
            raw = os.environ.get("MAX_REQUEST_BODY_BYTES")
            if raw is None:
                max_bytes = DEFAULT_MAX_BODY_BYTES
            else:
                try:
                    max_bytes = int(raw)
                except ValueError:
                    max_bytes = DEFAULT_MAX_BODY_BYTES
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if self.max_bytes is None or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        max_bytes = self.max_bytes
        for raw_name, raw_value in scope.get("headers") or []:
            if raw_name.decode("latin-1").lower() == "content-length":
                try:
                    declared = int(raw_value.decode("latin-1"))
                except ValueError:
                    declared = -1
                if declared > max_bytes:
                    await self._send_413(send)
                    return
                break

        running = {"total": 0}

        async def receive_wrapper():
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body") or b""
                running["total"] += len(body)
                if running["total"] > max_bytes:
                    await self._send_413(send)
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, receive_wrapper, send)

    @staticmethod
    async def _send_413(send) -> None:
        from json import dumps as _json_dumps

        body = _json_dumps(
            {"detail": "Request body exceeds maximum allowed size"}
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


import hmac as _hmac


def compare_api_key(submitted: Optional[str], expected: Optional[str]) -> bool:
    """Constant-time comparison for API keys (H-3).

    Returns ``False`` for empty/None inputs — never let a misconfigured
    deployment with an unset ``ROOT_API_KEY`` accept any client value.
    """
    if not submitted or not expected:
        return False
    submitted_b = submitted.encode("utf-8") if isinstance(submitted, str) else submitted
    expected_b = expected.encode("utf-8") if isinstance(expected, str) else expected
    return _hmac.compare_digest(submitted_b, expected_b)


def resolve_principal_from_api_key(api_key: Optional[str]) -> Optional[str]:
    """Map a submitted API key to one of (ROOT_ID, SYSTEM_ID, TEMPLATE_ID).

    Returns ``None`` when no key matches. Single source of truth for
    REST, GraphQL, and the Pydantic2FastAPI factory (H-6) — all three
    used to disagree on which keys map to which principal.
    """
    if not api_key:
        return None
    from serverframework.lib.Environment import env

    candidates = (
        ("ROOT_API_KEY", "ROOT_ID"),
        ("SYSTEM_API_KEY", "SYSTEM_ID"),
        ("TEMPLATE_API_KEY", "TEMPLATE_ID"),
    )
    for key_var, id_var in candidates:
        configured = env(key_var)
        if compare_api_key(api_key, configured):
            return env(id_var)
    return None


def resolve_client_ip(request_or_headers, peer_host: Optional[str] | None = None) -> Optional[str]:
    """Return the client IP, honouring `X-Forwarded-For` only when the
    immediate peer is a configured trusted proxy (H-7).

    Accepts either a Starlette/FastAPI ``Request``-shaped object (with
    ``client.host`` and ``headers``) or a plain mapping of headers when
    the caller already extracted the peer host.
    """
    if peer_host is None:
        client = getattr(request_or_headers, "client", None)
        peer_host = getattr(client, "host", None) if client else None
        if peer_host is None and hasattr(request_or_headers, "client") is False:
            # Plain header mapping path; no peer info available.
            peer_host = None
    if hasattr(request_or_headers, "headers"):
        headers = request_or_headers.headers
        get_header = headers.get
    else:
        get_header = lambda k, default=None: request_or_headers.get(k, default)  # noqa: E731

    if _peer_is_trusted_proxy(peer_host):
        xff = get_header("X-Forwarded-For", "") or ""
        forwarded = xff.split(",")[0].strip() if xff else ""
        if forwarded:
            return forwarded
    return peer_host


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
        retry_after_seconds: Optional[float] | None = None,
        scope: Optional[str] | None = None,
        actor_key: Optional[str] | None = None,
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

    def reset(self, key: Optional[str] | None = None) -> None:
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)


_inmemory_counter = _InMemoryCounter()


def _get_counter() -> Any:
    """Return the rate-limit counter backend.

    The default is `_inmemory_counter` — single-process correctness with the
    `incr(key, window_seconds) -> int` shape this middleware needs. Multi-
    process deployments wire a Redis-backed (or equivalent) counter that
    exposes the same shape via `set_rate_limit_counter(...)`.

    Note: Item 69's `DistributedCounter` is intentionally NOT used here —
    its API is quota-/sliding-window-consumption shaped (`try_consume`),
    not request-counter shaped. Conflating the two would force callers to
    bridge two semantically different primitives in the hot path.
    """
    return _rate_limit_counter or _inmemory_counter


_rate_limit_counter: Optional[Any] | None = None


def set_rate_limit_counter(counter: Any) -> None:
    """Wire a custom counter backend (e.g. Redis-backed) for multi-process
    consistency. The backend must expose `incr(key: str, window_seconds: int) -> int`
    returning the post-increment count for the sliding window. Pass `None`
    to revert to the in-memory fallback."""
    global _rate_limit_counter
    _rate_limit_counter = counter


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

    def __init__(self, policy: Optional[LockoutPolicy] | None = None) -> None:
        self.policy = policy or LockoutPolicy()
        self._failures: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)
        self._locked_until: Dict[Tuple[str, str], float] = {}
        self._lock = RLock()
        self._warn_if_multi_worker()

    @staticmethod
    def _warn_if_multi_worker() -> None:
        """L-5 — multi-worker deployments without a shared backend get N×
        the brute-force budget per actor (each worker keeps its own
        in-memory counter). Mirror the warning that ``ReplayCache``
        emits so operators see the failure mode at boot rather than
        after a brute-force incident.
        """
        try:
            from serverframework.lib.Environment import env as _env
        except Exception:
            return
        workers_raw = _env("UVICORN_WORKERS") or ""
        try:
            workers = int(workers_raw)
        except (TypeError, ValueError):
            workers = 1
        if workers > 1:
            logger.warning(
                "LockoutTracker is in-memory and per-process; UVICORN_WORKERS=%s "
                "multiplies the effective brute-force budget. Install a shared "
                "backend before serving production traffic.",
                workers,
            )

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


# ---------------------------------------------------------------------------
# Rate-limit registry + FastAPI middleware (Item 71b enforcement)
# ---------------------------------------------------------------------------


# Registry maps `(method, path)` -> (count, window_seconds, scope). Populated
# at app startup from `@rate_limit`-decorated handlers. Key is normalized
# `path` (FastAPI's templated form, e.g. `/v1/user/{id}`).
_RATE_LIMIT_REGISTRY: Dict[Tuple[str, str], Tuple[int, int, str]] = {}


def register_rate_limited_route(
    method: str, path: str, count: int, window_seconds: int, scope: str
) -> None:
    """Register a route's rate-limit policy.

    Called at app-startup time after route discovery — the FastAPI app
    walks every registered route and, when the endpoint callable carries
    `_rate_limit_count`/`_rate_limit_window_seconds`/`_rate_limit_scope`
    attributes (set by `@rate_limit(...)`), records the policy here.
    The middleware looks up `(method, path)` on every request.
    """
    if count <= 0 or window_seconds <= 0:
        raise ValueError(
            f"register_rate_limited_route: count and window_seconds must be > 0 "
            f"(got count={count}, window_seconds={window_seconds})"
        )
    _RATE_LIMIT_REGISTRY[(method.upper(), path)] = (count, window_seconds, scope)


def reset_rate_limit_state() -> None:
    """Test helper: clear the registry and the in-memory counter.

    Each test that exercises rate-limit enforcement should call this in
    setup so previous tests do not bleed counter state. The registry is
    also cleared so a `@rate_limit("1/min")` decorator stamped in one test
    does not gate the same path in the next.
    """
    _RATE_LIMIT_REGISTRY.clear()
    _inmemory_counter.reset()


def _resolve_actor_key(scope: str, request: Any) -> Optional[str]:
    """Compute the actor identity for a `(scope, request)` pair.

    Returns None when the scope cannot be resolved (e.g. `user` scope on an
    unauthenticated request). Caller decides what to do with None — the
    middleware skips enforcement so an anonymous request is never refused
    by a `scope='user'` rule it cannot satisfy.
    """
    if scope == "ip" or scope == "(ip, endpoint)":
        client = getattr(request, "client", None)
        peer_host = getattr(client, "host", None) if client else None
        # Only honour X-Forwarded-For when the immediate peer is a configured
        # trusted proxy. Otherwise an attacker can spoof the header to rotate
        # IPs and defeat per-IP rate limits including login throttling.
        host: Optional[str] = peer_host
        if _peer_is_trusted_proxy(peer_host):
            headers = getattr(request, "headers", {}) or {}
            xff = headers.get("X-Forwarded-For", "") if hasattr(headers, "get") else ""
            forwarded = xff.split(",")[0].strip() if xff else None
            if forwarded:
                host = forwarded
        if not host:
            return None
        return f"ip:{host}" if scope == "ip" else f"ip:{host}:{request.url.path}"

    if scope == "user" or scope == "(user, endpoint)":
        # RequestContext is the canonical user-id source per LIB.RequestContext.md.
        try:
            from serverframework.lib.RequestContext import get_request_user

            user = get_request_user()
            uid = user.get("user_id") if isinstance(user, dict) else None
        except Exception:
            uid = None
        if not uid:
            return None
        return f"user:{uid}" if scope == "user" else f"user:{uid}:{request.url.path}"

    if scope == "tenant":
        # Fail closed: a route author requesting tenant-scoped rate limits
        # has explicitly required tenant context. Returning None would skip
        # enforcement and let any header that breaks context resolution
        # bypass the limit. Use a sentinel so the middleware can refuse.
        try:
            from serverframework.lib.RequestContext import get_request_user

            user = get_request_user()
            tid = user.get("team_id") if isinstance(user, dict) else None
        except Exception:
            tid = None
        if not tid:
            return "tenant:__unresolved__"
        return f"tenant:{tid}"

    return None


class RateLimitMiddleware:
    """ASGI middleware that enforces `@rate_limit`-stamped policies.

    Lookups are O(1) on `(method, path)` from the registry. On a hit, the
    middleware computes the actor key (`scope`-dependent), increments the
    counter, and returns HTTP 429 with `Retry-After` if the count exceeds
    the policy. On a miss, the request flows untouched.

    Multi-process deployments must wire `DistributedCounter` from Item 69
    via `_get_counter()` for cross-process consistency. Single-process
    deployments and tests get correct semantics from the in-memory fallback.

    Attaches `request.state.rate_limited` (the `(count_used, count_limit)`
    tuple) for downstream handlers / logging that want to introspect.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope_dict: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope_dict.get("type") != "http":
            await self.app(scope_dict, receive, send)
            return

        method = scope_dict.get("method", "").upper()
        path = scope_dict.get("path", "")
        # Match against templated paths in the registry. The router populates
        # `scope_dict["route"]` once routing has resolved; before that we walk
        # the registry. Cheap enough for the typical sub-100-route surface.
        policy = _RATE_LIMIT_REGISTRY.get((method, path))
        if policy is None:
            # Try resolved-route path template if FastAPI has populated it.
            route = scope_dict.get("route")
            template = getattr(route, "path", None) if route else None
            if template:
                policy = _RATE_LIMIT_REGISTRY.get((method, template))

        if policy is None:
            await self.app(scope_dict, receive, send)
            return

        count_limit, window_seconds, rl_scope = policy

        # Build a tiny request facade for `_resolve_actor_key`. Avoids
        # importing FastAPI's Request here so the module stays lightweight.
        class _Req:
            client = type(
                "_C",
                (),
                {
                    "host": (
                        scope_dict.get("client", (None, None))[0]
                        if scope_dict.get("client")
                        else None
                    )
                },
            )()
            headers = {
                k.decode("latin-1").title(): v.decode("latin-1")
                for k, v in scope_dict.get("headers", [])
            }
            url = type("_U", (), {"path": path})()

        actor = _resolve_actor_key(rl_scope, _Req)
        if actor is None:
            # Cannot resolve actor (e.g. user-scope on anonymous request).
            # Skip enforcement rather than refuse a request the rule cannot
            # gate; the alternative is to reject every anonymous user
            # because of a scope mis-pick by the route author.
            await self.app(scope_dict, receive, send)
            return
        if actor == "tenant:__unresolved__":
            # Fail-closed sentinel from `_resolve_actor_key`: tenant-scoped
            # policies require tenant context. Refuse the request rather
            # than let header-crafting bypass the limit.
            from json import dumps as _json_dumps

            body = _json_dumps(
                {"detail": "Tenant scope unresolved"}
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        counter = _get_counter()
        # Both implementations expose `incr(key, window_seconds)`.
        key = f"rl:{method}:{path}:{rl_scope}:{actor}"
        try:
            current = counter.incr(key, window_seconds)
        except Exception:
            # Counter outage must not take the request path down. Fail open
            # with a logged warning; deployments that need fail-closed wire
            # a `DistributedCounter` whose `incr` is monitored separately.
            logger.warning(
                "Rate-limit counter unavailable; failing open",
                extra={"key": key},
            )
            await self.app(scope_dict, receive, send)
            return

        if current > count_limit:
            # 429 with Retry-After. The simple deque-based fallback does
            # not expose the oldest-entry timestamp cleanly, so we use the
            # window as the conservative bound.
            from json import dumps as _json_dumps

            body = _json_dumps(
                {
                    "detail": "Rate limit exceeded",
                    "scope": rl_scope,
                    "limit": count_limit,
                    "window_seconds": window_seconds,
                }
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"retry-after", str(window_seconds).encode("ascii")),
                        (
                            b"x-ratelimit-limit",
                            str(count_limit).encode("ascii"),
                        ),
                        (
                            b"x-ratelimit-remaining",
                            b"0",
                        ),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope_dict, receive, send)


def discover_rate_limited_routes(app: Any) -> int:
    """Walk a FastAPI app's router and register every `@rate_limit`-stamped
    endpoint. Returns the number of routes registered. Idempotent — calling
    twice with the same app produces the same registry contents.

    Called from `build_app` after every router has been mounted.
    """
    count = 0
    routes = getattr(app, "routes", None) or []
    for route in routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        spec_count = getattr(endpoint, "_rate_limit_count", None)
        if spec_count is None:
            continue
        window = getattr(endpoint, "_rate_limit_window_seconds", None)
        scope = getattr(endpoint, "_rate_limit_scope", None)
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or {"GET"}
        if window is None or scope is None or path is None:
            continue
        for method in methods:
            register_rate_limited_route(
                method=method,
                path=path,
                count=spec_count,
                window_seconds=window,
                scope=scope,
            )
            count += 1
    return count
