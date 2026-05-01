"""
Typed external error hierarchy and rotation policy primitives.

These types are the canonical contract between the Provider Rotation system
(`RotationManager`, `AbstractStaticProvider`) and the rest of the framework
(`AbstractExternalManager`, `AbstractBLLManager`, FastAPI/Strawberry layers).

A `*_via_provider` method that opts into the typed-raise contract (see
`AbstractExternalModel.raises_typed_errors` and the `@idempotent` decorator)
returns the raw payload on success and raises one of the
`BaseExternalError` subclasses on failure. The rotation system reads
`error.failure_class` (implicit from the type) to decide whether to retry,
back off, mark the provider unhealthy, or surface the error directly to the
caller.

Item 1 — Unified result contract / typed external errors.
Item 2 — RotationPolicy dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, ClassVar, Literal, Optional

from pydantic import BaseModel


class BaseExternalError(Exception):
    """
    Base class for all caller-facing external provider errors.

    Subclasses correspond to the four failure classes the rotation system
    distinguishes. Each subclass carries a class-level `runbook_url` that
    operators can follow when investigating a production incident.

    Attributes:
        message: Human-readable detail of the error.
        provider: Name of the provider that produced the error, when known.
        ability: Name of the ability or operation that was being performed.
        upstream_status: Numeric status code returned by the upstream, if any.
        upstream_payload: Raw payload returned by the upstream, if any.
        runbook_url: Class-level pointer to the operator runbook entry.
    """

    runbook_url: ClassVar[Optional[str]] = None

    def __init__(
        self,
        message: str = "",
        *,
        provider: Optional[str] = None,
        ability: Optional[str] = None,
        upstream_status: Optional[int] = None,
        upstream_payload: Any = None,
        cause: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.ability = ability
        self.upstream_status = upstream_status
        self.upstream_payload = upstream_payload
        self.cause = cause

    def __str__(self) -> str:
        parts = [self.message or self.__class__.__name__]
        if self.provider:
            parts.append(f"provider={self.provider}")
        if self.ability:
            parts.append(f"ability={self.ability}")
        if self.upstream_status is not None:
            parts.append(f"status={self.upstream_status}")
        return " | ".join(parts)


class TransientExternalError(BaseExternalError):
    """5xx-shaped failures. Retried per the provider's backoff policy; on
    exhaustion advances to the next provider in the chain."""

    runbook_url: ClassVar[Optional[str]] = (
        "https://docs.serverframework.dev/runbooks/transient-external-error"
    )


class AuthExternalError(BaseExternalError):
    """401 / 403 with auth-failure semantics. Marks the provider unhealthy
    for a cool-down window and advances without consuming retry budget."""

    runbook_url: ClassVar[Optional[str]] = (
        "https://docs.serverframework.dev/runbooks/auth-external-error"
    )


class InvalidInputExternalError(BaseExternalError):
    """4xx-shaped request defects (validation, missing required fields,
    operator-detected bad arguments). Never triggers rotation."""

    runbook_url: ClassVar[Optional[str]] = (
        "https://docs.serverframework.dev/runbooks/invalid-input-external-error"
    )


class RateLimitExternalError(BaseExternalError):
    """429-shaped failures. Honors `Retry-After` (or per-provider parser
    equivalent) and backs off against the same provider with exponential
    delay and jitter; does not advance the chain.

    Carries an optional `retry_after_seconds` indicating how long the caller
    or rotation system should wait before retrying.
    """

    runbook_url: ClassVar[Optional[str]] = (
        "https://docs.serverframework.dev/runbooks/rate-limit-external-error"
    )

    def __init__(
        self,
        message: str = "",
        *,
        retry_after_seconds: Optional[float] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after_seconds = retry_after_seconds


class PermanentExternalError(BaseExternalError):
    """An upstream response indicating the operation will never succeed
    against any provider in this chain (e.g., resource permanently
    deleted, account permanently suspended). Never retried."""

    runbook_url: ClassVar[Optional[str]] = (
        "https://docs.serverframework.dev/runbooks/permanent-external-error"
    )


# Specialized errors for narrower failure modes -------------------------------


class InvalidPaginationError(InvalidInputExternalError):
    """Raised when a pagination `next_token` is malformed or the embedded
    `query_hash` does not match the new query parameters."""


class UnsupportedOperatorError(InvalidInputExternalError):
    """Raised by a query DSL translator when it is asked to translate an
    operator the upstream does not support."""


class NavigationNotIncludedError(InvalidInputExternalError):
    """Raised when an external navigation property is accessed without
    being named in the request's `include` set, while strict mode is on."""


# RotationPolicy --------------------------------------------------------------


@dataclass
class RotationPolicy:
    """
    Per-provider rotation policy describing how each failure class is
    handled by the rotation system.

    Dispatch behavior (consumed by `RotationManager`, owned by Batch B):

    - `TransientExternalError`: retry up to `transient_max_retries` times
      against the same provider with exponential backoff between
      `transient_base_ms` and `transient_max_ms` (multiplied by a random
      `transient_jitter` factor in `[1 - jitter, 1 + jitter]`). On
      exhaustion, advance to the next provider in the chain.
    - `RateLimitExternalError`: honor `error.retry_after_seconds` if
      present, else fall back to exponential backoff between
      `rate_limit_base_ms` and `rate_limit_max_ms`. Stay on the same
      provider; do not advance the chain.
    - `AuthExternalError`: mark the provider unhealthy for
      `auth_cooldown_seconds`, advance to the next provider, do not
      consume the retry budget.
    - `InvalidInputExternalError` / `PermanentExternalError`: surface
      directly to the caller; do not retry; do not advance.

    `header_parser` is an optional callable that extracts a structured
    rate-limit signal (`retry_after_seconds`, `quota_remaining`, etc.)
    from the upstream response headers when the upstream uses a
    non-standard header shape.
    """

    transient_max_retries: int = 3
    transient_base_ms: int = 100
    transient_max_ms: int = 5000
    transient_jitter: float = 0.1
    rate_limit_base_ms: int = 1000
    rate_limit_max_ms: int = 60000
    auth_cooldown_seconds: int = 300
    header_parser: Optional[Callable[[Any], dict]] = field(default=None, repr=False)


def default_rotation_policy() -> RotationPolicy:
    """Return a `RotationPolicy` populated with the framework defaults.

    Provider authors that want the standard behavior can simply omit
    `rotation_policy = ...` on their class; the framework's RotationManager
    will fall back to this factory.
    """

    return RotationPolicy()


# ---- Item 48: Graceful degradation contract ----------------------------------


class DegradationMode(str, Enum):
    """Item 48 — three degradation modes that determine what the framework
    does when a non-critical provider exhausts its rotation chain.

    Values are string-typed so they round-trip cleanly through OpenAPI /
    audit logs / GraphQL introspection without a custom serializer.
    """

    FAIL_FAST = "fail_fast"
    """Default. Raise ``HTTPException(500)`` on rotation exhaustion. The
    operation surface is synchronous and the caller learns of failure
    immediately. Correct for transactional operations whose downstream
    behavior depends on the upstream's success (e.g. billing capture)."""

    QUEUE_AND_RETRY = "queue_and_retry"
    """Outbox-mediated. On rotation exhaustion the operation enrolls in
    the outbox (Item 35); the route returns ``202 Accepted`` with a
    tracking id; the outbox drain service performs the operation in the
    background; the client polls the tracking id (or subscribes via a
    webhook from Item 5) for completion. The SLA shifts from synchronous
    to asynchronous; clients calling such operations must know to handle
    202 responses. Correct for non-blocking sends like marketing email
    where eventual delivery is acceptable."""

    SILENT_DROP = "silent_drop"
    """Log and return success on rotation exhaustion. **Dangerous.** Only
    valid for genuinely fire-and-forget operations like analytics
    beacons. The framework emits a mandatory metric
    ``provider_silent_drop_total{provider, ability}`` on every
    silent-drop occurrence so operators can dashboard the rate; a
    non-zero rate on an operation that was not deliberately marked
    silent-drop is the alert signal for misconfiguration."""


@dataclass(frozen=True)
class DegradationPolicy:
    """Item 48 — per-ability degradation declaration consumed by the
    rotation system on chain exhaustion.

    Fields:
        mode: which of the three modes above applies.
        outbox_retention_days: how long the queue-and-retry outbox
            retains entries before considering them permanently failed
            (defaults to 7). Ignored unless ``mode == QUEUE_AND_RETRY``.
        outbox_max_attempts: maximum number of outbox-driven retries
            before the entry moves to the dead-letter queue (Item 35).
            Defaults to 5.

    The choice between modes is part of the API contract: switching an
    operation between ``FAIL_FAST`` and ``QUEUE_AND_RETRY`` is a breaking
    change because the response shape (200 vs 202) and the SLA
    (synchronous vs eventual) shift. Treat mode changes as version bumps
    per Item 39.
    """

    mode: DegradationMode = DegradationMode.FAIL_FAST
    outbox_retention_days: int = 7
    outbox_max_attempts: int = 5


def fail_fast() -> DegradationPolicy:
    """Convenience constructor for the default `FAIL_FAST` policy."""
    return DegradationPolicy(mode=DegradationMode.FAIL_FAST)


def queue_and_retry(
    *, outbox_retention_days: int = 7, outbox_max_attempts: int = 5
) -> DegradationPolicy:
    """Convenience constructor for `QUEUE_AND_RETRY` with explicit
    retention / attempt knobs."""
    return DegradationPolicy(
        mode=DegradationMode.QUEUE_AND_RETRY,
        outbox_retention_days=outbox_retention_days,
        outbox_max_attempts=outbox_max_attempts,
    )


def silent_drop() -> DegradationPolicy:
    """Convenience constructor for the dangerous `SILENT_DROP` policy.

    Authors using this must accept that successful return values will be
    emitted on actual upstream failure; the framework requires a
    deliberate opt-in via this constructor (rather than a bare
    ``DegradationPolicy(mode=SILENT_DROP)``) to make grep-ability
    explicit during code review.
    """
    return DegradationPolicy(mode=DegradationMode.SILENT_DROP)


def default_degradation_policy() -> DegradationPolicy:
    """Item 48 — the framework default is always `FAIL_FAST`.

    Provider authors that omit ``degradation_policy`` on their abilities
    inherit this default. Opting into the riskier modes requires an
    explicit declaration so degradation is visible in code review.
    """
    return DegradationPolicy()


@dataclass(frozen=True)
class QueuedForRetry:
    """Item 48 — sentinel returned by `RotationManager.rotate` when the
    active provider's `DegradationPolicy` is `QUEUE_AND_RETRY` and the
    rotation chain has been exhausted.

    Carries the outbox `tracking_id` so HTTP routes can render
    ``{"status": "accepted", "tracking_id": ...}`` with status 202.
    Callers that care about 202 semantics check
    ``isinstance(result, QueuedForRetry)``.
    """

    tracking_id: str
    status: str = "accepted"


@dataclass(frozen=True)
class SilentDropped:
    """Item 48 — sentinel returned by `RotationManager.rotate` when the
    active provider's `DegradationPolicy` is `SILENT_DROP` and the
    rotation chain has been exhausted. The framework logs and emits the
    `provider_silent_drop_total` metric before returning this sentinel.
    """

    provider: Optional[str] = None
    ability: Optional[str] = None


# Item 48 — OpenAPI surface model for QUEUE_AND_RETRY 202 responses.
class QueuedForRetryModel(BaseModel):
    """OpenAPI representation of a `QueuedForRetry` 202 response body."""

    tracking_id: str
    status: Literal["accepted"] = "accepted"


class SilentDroppedModel(BaseModel):
    """OpenAPI representation of a `SilentDropped` 200 response body."""

    status: Literal["silent_dropped"] = "silent_dropped"
    provider: Optional[str] = None
    ability: Optional[str] = None


__all__ = [
    "BaseExternalError",
    "TransientExternalError",
    "AuthExternalError",
    "InvalidInputExternalError",
    "RateLimitExternalError",
    "PermanentExternalError",
    "InvalidPaginationError",
    "UnsupportedOperatorError",
    "NavigationNotIncludedError",
    "RotationPolicy",
    "default_rotation_policy",
    "DegradationMode",
    "DegradationPolicy",
    "fail_fast",
    "queue_and_retry",
    "silent_drop",
    "default_degradation_policy",
    "QueuedForRetry",
    "SilentDropped",
    "QueuedForRetryModel",
    "SilentDroppedModel",
]
